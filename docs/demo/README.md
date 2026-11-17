# Demo assets

Everything here is fictional or synthetic. Nothing comes from a real applicant or a real company.

| File | What it is |
|---|---|
| `fixtures/demo_resume.yaml` | A fictional student's resume (same format as the app's resume). |
| `fixtures/demo_profile.yaml` | That student's graduation date and work authorization. |
| `fixtures/demo_posting.txt` | A synthetic internship posting for a made-up company. |
| `fixtures/api.json` | Every API response the demo screens read, recorded from one real Claude Haiku run ($0.012, 2 calls). |
| `record_fixtures.py` | Re-records `api.json`. The only step that spends money. |
| `screenshots/` | PNGs from the Playwright walkthrough. |
| `video/walkthrough.webm` | The walkthrough as a browser recording. |

## Regenerate screenshots and the video ($0)

```sh
cd web
npx playwright install chromium   # once
npm run demo                      # writes docs/demo/screenshots/ and docs/demo/video/walkthrough.webm
```

The walkthrough serves every API request from `fixtures/api.json` and blocks anything else, so it needs no API key, token or backend. It fails if the app requests something the fixture doesn't have.

## Re-record the fixture (about $0.01–0.02)

Only needed if the API's response shape changes. Run it from the repo root with `ANTHROPIC_API_KEY` in `.env`:

```sh
uv run python docs/demo/record_fixtures.py --live --force
```

It runs the real API in-process against a throwaway SQLite database, through the normal budget guard and cache.

## Make the video playable on GitHub

GitHub doesn't play `.webm` files committed to a repo. Pick one:

- **Upload it (no tools):** edit the README on github.com, drag `walkthrough.webm` (or an `.mp4`) into the editor, and paste the `user-attachments` link it gives you into the README's Demo section.
- **Convert locally** (needs ffmpeg, `brew install ffmpeg`):
  ```sh
  ffmpeg -i docs/demo/video/walkthrough.webm -c:v libx264 -pix_fmt yuv420p -movflags +faststart docs/demo/video/walkthrough.mp4
  ffmpeg -i docs/demo/video/walkthrough.webm -vf "fps=12,scale=390:-1:flags=lanczos" docs/demo/video/walkthrough.gif
  ```
