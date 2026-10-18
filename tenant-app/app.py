import os
from flask import Flask
from markupsafe import escape

app = Flask(__name__)
T = os.environ["TENANT_NAME"]

@app.get("/")
def home():
    return f"""<!doctype html><html><body style="font-family:system-ui;text-align:center;margin-top:15vh">
<!-- [[tenant:{escape(T)}]] -->
<h1>Welcome {escape(T)}</h1>
<p>This is <b>{escape(T)}</b>'s own isolated instance.</p>
</body></html>"""
