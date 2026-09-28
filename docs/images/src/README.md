# Regenerating the README images

Everything in `docs/images/` is generated. Rebuild it when the UI or hardware changes, so
the pictures keep telling the truth.

| Image | Made by |
|---|---|
| `banner-*.svg`, `architecture-*.svg` | `make_svgs.py` (light and dark variants) |
| `hoot-concept.png` | `render_concept.py` (needs `numpy` and `matplotlib`) |
| `dashboard-*.png`, `points-*.png`, `logs-*.png`, `settings-yaml-*.png` | `demo_unit.py` + `screenshots.py` |

```bash
cd docs/images
python src/make_svgs.py .
(cd src && python render_concept.py && mv hoot-concept.png ..)
```

### Screenshots

The screenshots come from a simulator-backed demo unit with 26 hours of seeded history,
captured with headless Google Chrome over the DevTools protocol (`pip install websockets`).
Adjust the `CHROME` path in `screenshots.py` for Linux or Windows.

```bash
mkdir -p /tmp/hoot-demo && cd /tmp/hoot-demo
export HOOT_WEB_PASSWORD="$(python -c 'import secrets; print(secrets.token_urlsafe(12))')"
echo "$HOOT_WEB_PASSWORD" > demo_password
python /path/to/hoot/docs/images/src/demo_unit.py /path/to/hoot     # writes config.yaml + data/
PYTHONPATH=/path/to/hoot python -m hoot -c config.yaml run &         # web UI on :18081
python /path/to/hoot/docs/images/src/screenshots.py /path/to/hoot/docs/images demo_password
kill %1
```

The demo data is synthetic. It comes from the `simulator` driver, not a real space.
