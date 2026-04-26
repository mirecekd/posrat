# POSRAT

Personal Online Study, Review & Assessment Tool — a Python (NiceGUI)
tool for authoring and practising certification exams (primarily AWS,
but the format is generic).

<div align="center">

[!["Buy Me A Coffee"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/mirecekdg) [!["PayPal.me"](https://www.paypalobjects.com/en_US/i/btn/btn_donateCC_LG.gif)](https://www.paypal.com/donate/?business=LJ5ZF7Q9KMTRW&no_recurring=0&currency_code=USD) 

</div>

## Features

- **Designer** — exam editor (single/multi-choice, hotspot, images, explanations).
- **Runner** — run exams in training or exam mode.
- **AI chat assistant** — optional Bedrock-backed study tutor via
  [Strands Agents](https://strandsagents.com/) with MCP server
  integration (defaults to
  [aws-knowledge-mcp](https://awslabs.github.io/mcp/servers/aws-knowledge-mcp-server/)).
  Configured in `/admin` → *AI chat* tab. Surfaces as a floating
  robot bottom-right in the Designer editor and in the Runner
  Training mode (never in Exam mode), plus a small robot icon in
  the header for generic AWS chat.

Each exam is stored as a single SQLite file; a portable `.posrat` bundle
(zip with `exam.json` + `assets/`) is used for import/export.

### AI chat — AWS credentials

The assistant uses the **default boto3 credential chain**
(`AWS_PROFILE`, environment variables, IMDS, SSO cache). No keys are
stored in POSRAT. Set `AWS_PROFILE=<your-profile>` before launching
`python -m posrat` (or when running the container), enable the chat in
`/admin` → *AI chat*, pick a Bedrock model id and region, and
optionally paste a Claude Desktop-shaped MCP JSON:

```json
{
  "mcpServers": {
    "aws-knowledge": {
      "url": "https://knowledge-mcp.global.api.aws"
    }
  }
}
```


## Run locally

```bash
workon posrat
pip install -e .
python -m posrat
```

The app listens on <http://localhost:8080>.

## Run with Docker

```bash
docker compose up -d --build
```

Or manually:

```bash
docker build -t posrat .
docker run -d -p 8080:8080 -v posrat-data:/data posrat
```

Data is persisted in the `/data` volume. The first admin account can be
bootstrapped via `POSRAT_ADMIN_USERNAME` / `POSRAT_ADMIN_PASSWORD`, or
created later:

```bash
docker exec -it posrat python -m posrat create-admin admin
```

Other env vars: `POSRAT_DATA_DIR` (default `/data`), `POSRAT_STORAGE_SECRET`
(stable cookie signing secret across restarts).

### Prebuilt image

A multi-arch image (linux/amd64, linux/arm64) is published to GHCR on every
push to `main` / tagged release:

```bash
docker pull ghcr.io/mirecekd/posrat:latest
```

## Support

If this tool is useful to you, you can support development:

<div align="center">

[!["Buy Me A Coffee"](https://www.buymeacoffee.com/assets/img/custom_images/orange_img.png)](https://www.buymeacoffee.com/mirecekdg) [!["PayPal.me"](https://www.paypalobjects.com/en_US/i/btn/btn_donateCC_LG.gif)](https://www.paypal.com/donate/?business=LJ5ZF7Q9KMTRW&no_recurring=0&currency_code=USD) 

</div>

## License

[MIT](LICENSE) — © 2026 Miroslav Dvorak.

