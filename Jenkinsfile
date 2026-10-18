pipeline {
  agent any
  options { timeout(time: 5, unit: 'MINUTES') }
  environment {
    DOMAIN = '65.1.108.129.nip.io'
    BASE   = '/opt/tenants'
  }

  parameters {
    string(name: 'TENANT', defaultValue: 'demo', description: 'Tenant name: lowercase letters, numbers, hyphens')
    choice(name: 'ACTION', choices: ['deploy', 'offboard'], description: 'Deploy or offboard tenant')
  }

  stages {

    stage('Validate input') {
      steps {
        sh '''#!/bin/bash
echo "$TENANT" | grep -Eq '^[a-z0-9]([a-z0-9-]{1,28}[a-z0-9])$' || { echo "INVALID tenant name"; exit 1; }
case "$TENANT" in portal|jenkins|www|admin|api|proxy|default|localhost) echo "reserved name"; exit 1;; esac
echo "name OK: $TENANT"
'''
      }
    }

    stage('Deploy tenant') {
      when { expression { params.ACTION == 'deploy' } }
      steps {
        sh '''#!/bin/bash
set -u
C="tenant-$TENANT"; HOST="$TENANT.$DOMAIN"
CONF="$BASE/conf.d/$TENANT.conf"; CRT="$BASE/certs/$TENANT"

exec 9>"/tmp/lock-$TENANT"; flock 9      # same tenant: ஒரு நேரத்துல ஒரு build
exec 8>/tmp/lock-nginx                   # nginx மாத்த: ஒரு நேரத்துல ஒரு build

rollback() {
  echo ">> rolling back $TENANT"
  flock 8
  rm -f "$CONF" "$CRT.crt" "$CRT.key"
  docker rm -f "$C" >/dev/null 2>&1
  docker exec proxy nginx -s reload >/dev/null 2>&1
  flock -u 8
}
fail() { echo "ERROR: $*"; rollback; exit 1; }

# R2: ஏற்கனவே ஓடுதா? அப்படின்னா எதையும் தொடாதே
if [ "$(docker inspect -f '{{.State.Running}}' "$C" 2>/dev/null)" = "true" ] && [ -f "$CONF" ]; then
  echo "ALREADY PROVISIONED - nothing changed"
  echo EXISTING > "/tmp/state-$TENANT"; exit 0
fi
echo NEW > "/tmp/state-$TENANT"

# 1. image build
flock /tmp/lock-build docker build -q -t tenant-app:1 tenant-app >/dev/null || fail "image build failed"

# 2. TLS certificate (R10)
openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
  -keyout "$CRT.key" -out "$CRT.crt" \
  -subj "/CN=$HOST" -addext "subjectAltName=DNS:$HOST" 2>/dev/null || fail "certificate failed"

# 3. container
docker rm -f "$C" >/dev/null 2>&1
docker run -d --name "$C" --network tenantnet --restart unless-stopped \
  -e TENANT_NAME="$TENANT" --memory 128m --cpus 0.25 \
  --read-only --tmpfs /tmp --cap-drop ALL --security-opt no-new-privileges \
  tenant-app:1 >/dev/null || fail "container start failed"

# 4. nginx config (test பண்ணாம reload இல்லை) (R7)
flock 8
sed -e "s/__T__/$TENANT/g" -e "s/__HOST__/$HOST/g" > "$CONF" <<'EOF'
server {
  listen 443 ssl;
  server_name __HOST__;
  ssl_certificate     /certs/__T__.crt;
  ssl_certificate_key /certs/__T__.key;
  location / {
    proxy_pass http://tenant-__T__:5000;
    proxy_set_header Host $host;
  }
}
EOF
docker exec proxy nginx -t || fail "nginx config rejected"
docker exec proxy nginx -s reload || fail "nginx reload failed"
flock -u 8
echo "PROVISIONED: https://$HOST"
'''
      }
    }

    stage('Verify tenant is really serving') {
      when { expression { params.ACTION == 'deploy' } }
      steps {
        sh '''#!/bin/bash
HOST="$TENANT.$DOMAIN"
for i in $(seq 1 15); do
  if curl -sk --max-time 3 --resolve "$HOST:443:127.0.0.1" "https://$HOST/" | grep -Fq "[[tenant:$TENANT]]"; then
    echo "VERIFIED: https://$HOST serves tenant $TENANT"; exit 0
  fi
  sleep 2
done
echo "VERIFY FAILED: $HOST is not serving $TENANT"; exit 1
'''
      }
    }

    stage('Offboard tenant') {
      when { expression { params.ACTION == 'offboard' } }
      steps {
        sh '''#!/bin/bash
C="tenant-$TENANT"; CONF="$BASE/conf.d/$TENANT.conf"; CRT="$BASE/certs/$TENANT"
exec 9>"/tmp/lock-$TENANT"; flock 9
exec 8>/tmp/lock-nginx; flock 8
rm -f "$CONF"
docker exec proxy nginx -t && docker exec proxy nginx -s reload
docker rm -f "$C" >/dev/null 2>&1
rm -f "$CRT.crt" "$CRT.key" "/tmp/state-$TENANT"
flock -u 8
left=0
docker ps -a --format '{{.Names}}' | grep -qx "$C" && { echo "container still exists"; left=1; }
[ -e "$CONF" ] && { echo "proxy conf still exists"; left=1; }
[ -e "$CRT.crt" ] && { echo "certificate still exists"; left=1; }
[ $left = 0 ] && echo "OFFBOARDED: nothing left behind for $TENANT"
exit $left
'''
      }
    }
  }

  post {
    success {
      sh '''#!/bin/bash
echo "$TENANT" | grep -Eq '^[a-z0-9]([a-z0-9-]{1,28}[a-z0-9])$' || exit 0
. /opt/tenants/secrets.env
if [ "$ACTION" = deploy ]; then S=ACTIVE; else S=DELETED; fi
curl -fsS -X POST http://localhost:5000/internal/status -H "X-Token: $CALLBACK_TOKEN" \
  -H 'Content-Type: application/json' -d '{"tenant":"'"$TENANT"'","status":"'"$S"'"}'
'''
    }
    failure {
      sh '''#!/bin/bash
echo "$TENANT" | grep -Eq '^[a-z0-9]([a-z0-9-]{1,28}[a-z0-9])$' || exit 0
. /opt/tenants/secrets.env
if [ "$ACTION" = deploy ]; then
  S=FAILED
  # புதுசா create ஆகி verify fail ஆனா dead tenant-ஐ சுத்தம் பண்ணு (ஏற்கனவே இருந்ததை தொடமாட்டோம்)
  if [ "$(cat /tmp/state-$TENANT 2>/dev/null)" = NEW ]; then
    exec 8>/tmp/lock-nginx; flock 8
    rm -f "$BASE/conf.d/$TENANT.conf" "$BASE/certs/$TENANT.crt" "$BASE/certs/$TENANT.key"
    docker rm -f "tenant-$TENANT" >/dev/null 2>&1
    docker exec proxy nginx -s reload >/dev/null 2>&1
    flock -u 8
  fi
else
  S=DELETE_FAILED
fi
curl -fsS -X POST http://localhost:5000/internal/status -H "X-Token: $CALLBACK_TOKEN" \
  -H 'Content-Type: application/json' -d '{"tenant":"'"$TENANT"'","status":"'"$S"'"}'
'''
    }
  }
}
