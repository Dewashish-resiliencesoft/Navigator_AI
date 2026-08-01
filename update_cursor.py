import base64
import re

with open("assets/custom cursor/navigator_cursor.png", "rb") as f:
    b64_data = base64.b64encode(f.read()).decode("utf-8")

data_uri = f"data:image/png;base64,{b64_data}"

with open("navigator/automation/browser/cursor.py", "r") as f:
    content = f.read()

# Replace the cursor CSS
old_css = """  c.style.cssText = [
    'position:fixed', 'left:0', 'top:0', 'width:18px', 'height:18px',
    'border-radius:50%', 'border:2px solid #0a5c31',
    'background:rgba(10,92,49,0.35)', 'pointer-events:none',
    'z-index:2147483647', 'transform:translate(-50%,-50%)',
    'transition:left 80ms linear, top 80ms linear',
  ].join(';');"""

new_css = f"""  c.style.cssText = [
    'position:fixed', 'left:0', 'top:0', 'width:27px', 'height:32px',
    'background-image:url("{data_uri}")',
    'background-size:contain', 'background-repeat:no-repeat',
    'pointer-events:none',
    'z-index:2147483647', 'transform:translate(-5%,-4%)',
    'transition:left 80ms linear, top 80ms linear',
  ].join(';');"""

if old_css in content:
    content = content.replace(old_css, new_css)
else:
    print("Warning: old_css not found")

with open("navigator/automation/browser/cursor.py", "w") as f:
    f.write(content)
