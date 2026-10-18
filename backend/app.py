cat > backend/app.py <<'FILE_END'
import os, re, time, hmac, boto3, requests
from botocore.exceptions import ClientError
from flask import Flask, request, jsonify, Response

app = Flask(__name__)

NAME_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{1,28}[a-z0-9])$")
RESERVED = {"portal", "jenkins", "www", "admin", "api", "proxy", "default", "localhost"}
DOMAIN = os.environ["BASE_DOMAIN"]
JURL = os.environ["JENKINS_URL"]
JAUTH = (os.environ["JENKINS_USER"], os.environ["JENKINS_TOKEN"])
CB_TOKEN = os.environ["CALLBACK_TOKEN"]

ddb = boto3.resource("dynamodb", endpoint_url=os.getenv("DYNAMO_ENDPOINT") or None)

def init_table():
    for _ in range(40):
        try:
            ddb.create_table(
                TableName="tenants",
                KeySchema=[{"AttributeName": "tenant", "KeyType": "HASH"}],
                AttributeDefinitions=[{"AttributeName": "tenant", "AttributeType": "S"}],
                BillingMode="PAY_PER_REQUEST")
            return
        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceInUseException":
                return
            raise
        except Exception:
            time.sleep(1)

init_table()
table = ddb.Table("tenants")


def valid(n):
    return bool(NAME_RE.match(n)) and n not in RESERVED

def now():
    return int(time.time())

def get(n):
    return table.get_item(Key={"tenant": n}).get("Item")

def claim(n):
    try:
        table.put_item(Item={"tenant": n, "status": "PROVISIONING", "created_at": now(), "updated_at": now()},
                       ConditionExpression="attribute_not_exists(tenant)")
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise

def transition(n, new, expected=None):
    vals = {":n": new, ":t": now()}
    kw = dict(Key={"tenant": n}, UpdateExpression="SET #s=:n, updated_at=:t",
              ExpressionAttributeNames={"#s": "status"})
    if expected:
        ph = []
        for i, e in enumerate(expected):
            vals[f":e{i}"] = e
            ph.append(f":e{i}")
        kw["ConditionExpression"] = "#s IN (" + ",".join(ph) + ")"
    kw["ExpressionAttributeValues"] = vals
    try:
        table.update_item(**kw)
        return True
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            return False
        raise

def start(n, action, msg):
    try:
        r = requests.post(f"{JURL}/job/tenant-deploy/buildWithParameters",
                          params={"TENANT": n, "ACTION": action}, auth=JAUTH, timeout=10)
        r.raise_for_status()
        return jsonify(tenant=n, message=msg), 202
    except Exception as e:
        transition(n, "FAILED" if action == "deploy" else "DELETE_FAILED")
        return jsonify(error="could not trigger Jenkins", detail=str(e)), 502

def ensure(n, create):
    if create and claim(n):
        return start(n, "deploy", "signup accepted, pipeline triggered")
    item = get(n)
    if not item:
        return jsonify(error="unknown tenant, please sign up first"), 404
    st = item["status"]
    if st == "ACTIVE":
        return jsonify(status=st, url=f"https://{n}.{DOMAIN}",
                       message="already provisioned - nothing redeployed"), 200
    if st == "PROVISIONING":
        return jsonify(status=st, message="provisioning in progress"), 202
    if st == "FAILED":
        if transition(n, "PROVISIONING", ["FAILED"]):
            return start(n, "deploy", "previous attempt failed, retrying")
        return jsonify(status="PROVISIONING", message="retry already running"), 202
    return jsonify(status=st, message="tenant is being removed"), 409

def name_from_body():
    n = str((request.get_json(silent=True) or {}).get("tenant", "")).strip().lower()
    return n if valid(n) else None


@app.post("/api/signup")
def signup():
    n = name_from_body()
    if not n:
        return jsonify(error="invalid name: 3-30 chars, a-z 0-9 and '-' only"), 400
    return ensure(n, True)

@app.post("/api/login")
def login():
    n = name_from_body()
    if not n:
        return jsonify(error="invalid name"), 400
    return ensure(n, False)

@app.get("/api/tenants/<n>")
def status(n):
    if not valid(n):
        return jsonify(error="invalid name"), 400
    item = get(n)
    if not item:
        return jsonify(error="not found"), 404
    out = {"status": item["status"]}
    if item["status"] == "ACTIVE":
        out["url"] = f"https://{n}.{DOMAIN}"
    return jsonify(out)

@app.delete("/api/tenants/<n>")
def offboard(n):
    if not valid(n):
        return jsonify(error="invalid name"), 400
    if not get(n):
        return jsonify(error="not found"), 404
    if transition(n, "DELETING", ["ACTIVE", "FAILED", "DELETE_FAILED"]):
        return start(n, "offboard", "offboarding started")
    return jsonify(error="tenant busy"), 409

@app.post("/internal/status")
def callback():
    if not hmac.compare_digest(request.headers.get("X-Token", ""), CB_TOKEN):
        return jsonify(error="forbidden"), 403
    d = request.get_json(silent=True) or {}
    n, st = str(d.get("tenant", "")), d.get("status")
    if not valid(n) or st not in {"ACTIVE", "FAILED", "DELETED", "DELETE_FAILED"}:
        return jsonify(error="bad request"), 400
    if st == "DELETED":
        table.delete_item(Key={"tenant": n})
    else:
        transition(n, st)
    return jsonify(ok=True)


PAGE = """<!doctype html>
<html><head><meta charset="utf-8"><title>Tenant Portal</title>
<style>
body{font-family:system-ui,sans-serif;max-width:520px;margin:70px auto;padding:0 16px;text-align:center}
input{width:100%;padding:12px;font-size:17px;box-sizing:border-box}
button{padding:10px 18px;margin:12px 4px;font-size:15px;cursor:pointer}
#msg{margin-top:20px;font-size:16px}
</style></head>
<body>
<h2>Tenant Portal</h2>
<input id="t" placeholder="your name (e.g. vairamuthu)" autocomplete="off">
<button onclick="go('/api/signup')">Sign Up</button>
<button onclick="go('/api/login')">Login</button>
<button onclick="off()">Offboard</button>
<div id="msg"></div>
<script>
const $ = id => document.getElementById(id);
const name = () => $('t').value.trim().toLowerCase();
const show = m => $('msg').innerHTML = m;
const sleep = ms => new Promise(r => setTimeout(r, ms));

async function api(method, path, body) {
  const r = await fetch(path, {method, headers: {'Content-Type': 'application/json'},
                               body: body ? JSON.stringify({tenant: name()}) : undefined});
  return {code: r.status, j: await r.json().catch(() => ({}))};
}
async function go(path) {
  const {code, j} = await api('POST', path, true);
  if (code >= 400) { show('&#10060; ' + (j.error || j.message)); return; }
  show('&#9203; ' + (j.message || 'working...'));
  for (let i = 0; i < 90; i++) {
    const s = await api('GET', '/api/tenants/' + encodeURIComponent(name()));
    if (s.j.status === 'ACTIVE') {
      show('&#9989; Ready! Opening <a href="' + s.j.url + '">' + s.j.url + '</a> ...');
      setTimeout(() => location.href = s.j.url, 2000); return;
    }
    if (s.j.status === 'FAILED') { show('&#10060; Deployment failed. Check Jenkins.'); return; }
    await sleep(2000);
  }
  show('Still working, check Jenkins.');
}
async function off() {
  const {code, j} = await api('DELETE', '/api/tenants/' + encodeURIComponent(name()));
  if (code !== 202) { show('&#10060; ' + (j.error || j.message)); return; }
  show('&#9203; Removing...');
  for (let i = 0; i < 60; i++) {
    const s = await api('GET', '/api/tenants/' + encodeURIComponent(name()));
    if (s.code === 404) { show('&#9989; Tenant removed completely'); return; }
    await sleep(2000);
  }
}
</script></body></html>"""

@app.get("/")
def home():
    return Response(PAGE, mimetype="text/html")
FILE_END
