# Share the live dashboard on trusted Wi-Fi

Run these commands from the repository folder on the model owner's Windows laptop.
Keep the model and database local; only dashboard port 8000 needs sharing.

1. Start Docker Desktop, then run `docker compose up -d` and
   `.\scripts\setup-local-model.ps1 -StartOnly` if the dependencies are not already running.
2. Stop an existing dashboard with Ctrl+C in its terminal. Start the updated server:

   ```powershell
   .\.venv\Scripts\python -m web.server --host 0.0.0.0 --port 8000 --password-prompt
   ```

   Enter and confirm a unique temporary password of at least 12 characters.
   Input is hidden. The password stays in this server process, not in a file or
   shell history. Leave this terminal running.
3. On your laptop, open `http://127.0.0.1:8000`. The browser prompts for username
   `teammate` and the password you chose. Confirm the live panel is ready.
4. In another terminal, run `ipconfig`. Find the **IPv4 Address** under your
   connected Wi-Fi adapter (WLAN), not the WSL/Hyper-V adapter.
   Both laptops can open `http://<that IPv4 address>:8000` with the real address
   substituted. `0.0.0.0` is a listening setting, not the URL to open.
5. Give your teammate the URL, username, and password. They choose **Ask it live**,
   type a database question, and click **Run both arms**. They need only a browser.

## If your teammate cannot connect

If localhost works but your teammate's browser times out, check Windows Firewall
and Wi-Fi client isolation. The password prompt happens after connection, so it
cannot fix a timeout. A 401 response means the server is reachable but requires login.

For a narrow firewall exception, get your teammate's Wi-Fi IPv4 address from
their network settings. In **PowerShell as Administrator** on your laptop:

```powershell
$teammateIP = Read-Host 'Teammate Wi-Fi IPv4 address'
New-NetFirewallRule -Name 'HarnessDashboardTemp' -DisplayName 'Harness dashboard temporary teammate access' -Direction Inbound -Action Allow -Protocol TCP -LocalPort 8000 -RemoteAddress $teammateIP -InterfaceAlias 'WLAN' -Profile Any
```

Replace `WLAN` if your Wi-Fi interface has a different name. `-Profile Any`
also covers Wi-Fi classified as Public; access is still restricted to the
specified teammate IP, interface, and port. Do not disable Windows Firewall.
IP addresses can change when either device reconnects.

If the correct address still times out after the firewall exception, the access
point may block device-to-device traffic. Use a trusted personal hotspot or a
private VPN such as Tailscale. Being on the same SSID alone does not establish reachability.

## End or change access

Stop the dashboard with Ctrl+C. To share again, rerun with `--password-prompt`
and choose a new password. To go back to local-only use, run
`.\.venv\Scripts\python -m web.server` without sharing flags.
Remove the temporary firewall rule in an Administrator terminal:

```powershell
Remove-NetFirewallRule -Name 'HarnessDashboardTemp'
```

The browser may cache Basic login credentials; use a private window when testing
a changed password. A configured password protects all static files and API routes.
Anyone with the password can log in; the firewall IP restriction further limits
which device can connect. HTTP does not encrypt the password, questions, or results:
use this only on trusted Wi-Fi with a throwaway password, and use HTTPS for untrusted networks.

Only one live question can run at a time. Your laptop must stay awake with the
dashboard, model, and database running. The GitHub Pages URL remains a saved-results dashboard.
