"""Capture README screenshots of the running demo unit via headless Chrome (CDP)."""
import asyncio, base64, json, subprocess, sys, tempfile, time, urllib.request
from pathlib import Path
import websockets

OUT = Path(sys.argv[1]); PW = Path(sys.argv[2]).read_text().strip()
BASE = "http://127.0.0.1:18081/"
CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
AUTH = "Basic " + base64.b64encode(f"hoot:{PW}".encode()).decode()

proc = subprocess.Popen([CHROME, "--headless=new", "--remote-debugging-port=9333",
                         f"--user-data-dir={tempfile.mkdtemp(prefix='hoot-shots-')}", "--no-first-run",
                         "--hide-scrollbars", "about:blank"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(50):
    try:
        targets = json.load(urllib.request.urlopen("http://127.0.0.1:9333/json"))
        page = next(t for t in targets if t["type"] == "page"); break
    except Exception:
        time.sleep(0.2)

async def main():
    async with websockets.connect(page["webSocketDebuggerUrl"], max_size=2**28) as ws:
        n = 0
        async def cdp(method, **params):
            nonlocal n; n += 1; my = n
            await ws.send(json.dumps({"id": my, "method": method, "params": params}))
            while True:
                msg = json.loads(await ws.recv())
                if msg.get("id") == my:
                    if "error" in msg: raise RuntimeError(msg["error"])
                    return msg.get("result", {})
        async def js(expr):
            r = await cdp("Runtime.evaluate", expression=expr, awaitPromise=True, returnByValue=True)
            return r.get("result", {}).get("value")

        await cdp("Network.enable")
        await cdp("Network.setExtraHTTPHeaders", headers={"Authorization": AUTH})
        async def shot(name, scheme, width, height, setup, clip_sel=None):
            await cdp("Emulation.setDeviceMetricsOverride", width=width, height=height,
                      deviceScaleFactor=2, mobile=False)
            await cdp("Emulation.setEmulatedMedia",
                      features=[{"name": "prefers-color-scheme", "value": scheme}])
            await cdp("Page.navigate", url=BASE)
            await asyncio.sleep(2.5)
            await js(f"(async () => {{ {setup} }})()")
            await asyncio.sleep(2.0)
            params = {"format": "png"}
            if clip_sel:
                if clip_sel == "page":
                    box = await js("(() => { const f = document.querySelector('footer').getBoundingClientRect();"
                                   " return {x: 12, y: 12, w: innerWidth - 24, h: f.bottom - 12}; })()")
                else:
                    box = await js(f"(() => {{ const r = document.querySelector('{clip_sel}').getBoundingClientRect();"
                                   " return {x: r.x, y: r.y + scrollY, w: r.width, h: r.height}; })()")
                pad = 12
                params["clip"] = {"x": box["x"] - pad, "y": box["y"] - pad, "width": box["w"] + 2 * pad,
                                  "height": box["h"] + 2 * pad, "scale": 1}
                params["captureBeyondViewport"] = True
            data = await cdp("Page.captureScreenshot", **params)
            (OUT / f"{name}.png").write_bytes(base64.b64decode(data["data"]))
            print("wrote", name)

        trend24 = ("document.querySelector('#trendHours').value='24';"
                   "document.querySelector('#trendChannel').value='co2'; await loadTrend();")
        await shot("dashboard-light", "light", 1200, 1000, trend24, "page")
        await shot("dashboard-dark", "dark", 1200, 1000, trend24, "page")
        # Panel shots hide the reading cards so no sliver of them lands in the crop.
        tab = lambda t: ("document.querySelector('#cards').style.display='none';"
                         f"document.querySelector('[data-tab={t}]').click(); await new Promise(r=>setTimeout(r,800));")
        for scheme in ("light", "dark"):
            await shot(f"points-{scheme}", scheme, 1200, 900, tab("points"), ".panel")
            await shot(f"logs-{scheme}", scheme, 1200, 1200, tab("logs"), ".panel")
            await shot(f"settings-yaml-{scheme}", scheme, 1200, 1600,
                       tab("config") + "document.querySelector('#yamlBox').open = true;"
                       "await new Promise(r=>setTimeout(r,800)); document.querySelector('#yamlText').style.minHeight='300px';"
                       "document.querySelector('#yamlText').style.height='300px'; await sendYaml(true);", "#yamlBox")

try:
    asyncio.run(main())
finally:
    proc.terminate()
