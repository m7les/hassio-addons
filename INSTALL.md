# Publishing this repository to GitHub

Step-by-step from zero. Replace `m7les` with your actual GitHub username throughout.

## 1. Replace placeholders

Before pushing, edit these to match your GitHub username and email:

- `repository.yaml` — `url`, `maintainer`
- `README.md` — every URL with `m7les`
- `LICENSE` — `Copyright (c) 2026 Myles`
- `lifx_switch_bridge/config.yaml` — the `url` line, and the commented `image` line
- `lifx_switch_bridge/build.yaml` — the `image.source` label

Quick way on Linux / macOS:

```bash
cd hassio-addons
# Replace 'm7les' with YOUR username:
find . -type f \( -name '*.yaml' -o -name '*.md' -o -name 'LICENSE' \) \
  -exec sed -i.bak 's|m7les|YOUR_USERNAME_HERE|g' {} +
find . -name '*.bak' -delete
# Update your email in repository.yaml manually.
```

## 2. Create the GitHub repo

1. Go to <https://github.com/new>.
2. Repository name: `hassio-addons`.
3. Public.
4. **Don't** initialize with README / .gitignore / license — we already have them.
5. Click "Create repository".

## 3. Push the code

From inside the unzipped `hassio-addons` directory:

```bash
git init
git add .
git commit -m "Initial commit: LIFX Switch Bridge add-on"
git branch -M main
git remote add origin https://github.com/YOUR_USERNAME_HERE/hassio-addons.git
git push -u origin main
```

## 4. Install in Home Assistant (build-locally mode — works immediately)

1. Open HA -> Settings -> Add-ons -> Add-on Store.
2. Three-dot menu (top right) -> Repositories.
3. Paste: `https://github.com/YOUR_USERNAME_HERE/hassio-addons`
4. Add, close.
5. Refresh the store. A new section "Myles' Home Assistant Add-ons" should appear with the add-on inside.
6. Install. Supervisor builds the image locally — first install takes 1-2 minutes.
7. Configure if needed, start.

This is the working state. Everything below is optional polish.

## 5. (Optional) Enable pre-built images via GHCR

This makes future installs near-instant for you and anyone else. Skip if you're the only user.

1. **Uncomment the `image:` line** in `lifx_switch_bridge/config.yaml`. It should read:

   ```yaml
   image: "ghcr.io/YOUR_USERNAME_HERE/hassio-addons/{arch}-lifx_switch_bridge"
   ```

2. Commit and push:

   ```bash
   git add lifx_switch_bridge/config.yaml
   git commit -m "Use pre-built GHCR images"
   git push
   ```

3. The push triggers the GitHub Action (.github/workflows/build.yml) which builds and publishes amd64 / aarch64 / armv7 images to ghcr.io. Check progress at <https://github.com/YOUR_USERNAME_HERE/hassio-addons/actions>.

4. After the action completes, go to <https://github.com/YOUR_USERNAME_HERE?tab=packages>. You'll see three new packages (one per arch). For each: click the package -> Package settings (right side) -> "Change visibility" at the bottom -> Public. This is required so HA can pull them without auth.

5. Reinstall the add-on in HA. The supervisor now pulls the pre-built image instead of building. ~10 seconds.

## 6. (Optional) Bump version and release

When you make changes:

1. Edit `lifx_switch_bridge/config.yaml`, bump `version:`.
2. Commit + push.
3. If pre-built images are enabled, the workflow rebuilds automatically.
4. In HA -> Add-on -> click "Update" (or wait for periodic check).

## Troubleshooting

**Add-on doesn't appear after adding the repo URL.** YAML error in `config.yaml` or `repository.yaml`. Check Settings -> System -> Logs -> "Supervisor" dropdown.

**Build fails with permission denied.** The first run of the GitHub Action needs the repo's Settings -> Actions -> General -> Workflow permissions set to "Read and write permissions". GitHub defaults to read-only for new repos.

**Pull fails after enabling `image:`.** The GHCR packages are still private. See step 5.4.

**"No switches found via Interactor at ..."** The Photons Interactor isn't reachable from this add-on. Open the bridge's Configuration tab and try alternative URLs:
- `http://homeassistant.local:6100` (default)
- `http://supervisor:6100` (internal proxy)
- `http://192.168.x.x:6100` (direct LAN IP)
