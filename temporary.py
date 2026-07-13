python3 << 'PYEOF'
with open("/home/zawayix/xorics-ai/bridge.py") as f:
    content = f.read()

content = content.replace(
    '[HERMES_BIN, "chat",\n         "--continue", _BRIDGE_SESSION,',
    '[HERMES_BIN, "chat",'
)

with open("/home/zawayix/xorics-ai/bridge.py", "w") as f:
    f.write(content)

import subprocess
r = subprocess.run(["python3", "-c", "import ast; ast.parse(open('/home/zawayix/xorics-ai/bridge.py').read())"], capture_output=True, text=True)
print("Syntax OK" if r.returncode == 0 else f"ERROR: {r.stderr[:200]}")
PYEOF
