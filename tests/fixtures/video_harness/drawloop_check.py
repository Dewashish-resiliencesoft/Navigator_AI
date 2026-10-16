"""Run the bot's share draw loop (override JS) in real Chromium: a detached
<video> fed by a 20fps stream must be drawn at ~source fps."""
import sys
from pathlib import Path
from playwright.sync_api import sync_playwright
js = Path(sys.argv[1]).read_text()
html = """<script>
class S { constructor(){ this.canvas=document.createElement('canvas'); this.canvasCtx=this.canvas.getContext('2d'); }
__JS__
}
window.logs=[]; console.log=(m)=>window.logs.push(m);
window.run=async()=>{ const src=document.createElement('canvas'); src.width=1280; src.height=720; const c=src.getContext('2d');
 let n=0; setInterval(()=>{c.fillStyle='#'+((n++*99999)%0xffffff).toString(16).padStart(6,'0'); c.fillRect(0,0,1280,720);},50);
 const v=document.createElement('video'); v.muted=true; v.srcObject=src.captureStream(20); await v.play();
 const s=new S(); s.videoElement=v; const orig=s.canvasCtx.drawImage.bind(s.canvasCtx); window.draws=0;
 s.canvasCtx.drawImage=(...a)=>{window.draws++; return orig(...a);}; s._startVideoDrawingLoop(); }
</script>""".replace("__JS__", js)
with sync_playwright() as p:
    b = p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
    pg = b.new_page(); pg.set_content(html); pg.evaluate("run()")
    pg.wait_for_timeout(1000); d0 = pg.evaluate("draws"); pg.wait_for_timeout(5000); d1 = pg.evaluate("draws")
    print({"draw_fps": round((d1 - d0) / 5, 1), "logs": pg.evaluate("logs")})
    b.close()
