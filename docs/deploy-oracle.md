# Running the bot 24/7 on Oracle Cloud (free tier)

This guide puts the paper-trading bot on a small Linux server in Oracle's cloud, so it runs while
your PC is off. Expected cost: **0 kr** if you only use resources marked *Always Free*.

You do the account steps yourself; the scripts in `deploy/` do the installing.

Placeholders: `<server ip>` is your server's public IP. Never commit it or paste it in issues.

---

## 1. Create the account

1. Sign up at oracle.com/cloud/free. A credit card is needed to verify you; Always Free resources
   are not charged.
2. **Home region:** pick *Sweden Central (Stockholm)* if offered. It cannot be changed later, and
   Always Free servers only exist in the home region.
3. Turn on 2-step verification for the Oracle account.

### Idle servers (read this)
Oracle may reclaim Always Free servers that are "idle": over 7 days, CPU, network (and memory on
Ampere) usage below 20 %. Our bot uses far less than that, so this **will** apply to us.
Options:
- **A. Upgrade the account to Pay As You Go** (recommended). Idle reclaiming then stops, and Always
  Free resources stay free. The risk: anything you create that is *not* Always Free gets billed.
  Protect yourself with a budget alert (Billing -> Budgets -> 1 USD, alert at 1 %).
- **B. Stay on the free account** and accept that Oracle may stop the server. You would notice
  because the daily report email stops arriving. Everything on disk survives a stop.

Decide before step 3. Do not run "keep busy" scripts to fool the check.

## 2. Make an SSH key on your PC

SSH keys let you log in without a password. In PowerShell:

```powershell
ssh-keygen -t ed25519 -f $HOME\.ssh\oracle_cryptobot -C cryptobot
```

Give it a passphrase. This creates `oracle_cryptobot` (private, never share) and
`oracle_cryptobot.pub` (public, goes to Oracle).

## 3. Create the server

Oracle Console -> Compute -> Instances -> **Create instance**:

| Setting | Choose |
|---|---|
| Name | `cryptobot` |
| Image | **Canonical Ubuntu 24.04** |
| Shape | **VM.Standard.E2.1.Micro** (AMD, 1 GB, *Always Free-eligible*). If **VM.Standard.A1.Flex** is available, 1 OCPU / 6 GB is better; it is often "out of capacity". |
| Networking | Create new VCN and subnet, **assign a public IPv4 address** |
| SSH keys | *Upload public key*: `oracle_cryptobot.pub` |
| Boot volume | Default (stays within the free 200 GB) |

Check that every item shows *Always Free-eligible* before you click Create.

The default firewall (security list) only lets SSH (port 22) in. Keep it that way: the bot only
makes outgoing connections.

Note the **public IP address** shown on the instance page. Bitvavo's API key will be locked to it
later, so do not terminate the instance unless you mean to (a new one gets a new IP).

## 4. Connect once to test

In PowerShell (set the IP once per session, or add the line to your PowerShell profile):

```powershell
$env:CRYPTOBOT_SERVER = "<server ip>"
ssh -i $HOME\.ssh\oracle_cryptobot ubuntu@$env:CRYPTOBOT_SERVER
```

Answer `yes` to the fingerprint question the first time, then type `exit`.

## 5. Fill in `.env` on your PC

Copy `.env.example` to `.env` in the project folder and fill in the Gmail lines:
- `ALERT_EMAIL_FROM` / `ALERT_EMAIL_TO`: your Gmail address
- `ALERT_EMAIL_APP_PASSWORD`: a Gmail app password (Google account -> Security ->
  2-Step Verification -> App passwords). Not your normal password.

Leave the Bitvavo lines empty: paper trading does not need keys.

## 6. Install the bot

From the project folder, with everything committed (only committed code is sent):

```powershell
.\deploy\deploy.ps1 -WithEnv
```

This copies the code and `.env`, installs Python packages, runs the tests (installation stops if
any fail), and starts the `cryptobot` service. It takes a few minutes the first time.

Within a minute you should get a **"Bot started"** email.

## 7. Everyday use

```powershell
.\deploy\remote.ps1 status       # running?
.\deploy\remote.ps1 logs         # what is it doing?
.\deploy\remote.ps1 decisions    # last decisions and why
.\deploy\remote.ps1 trades       # paper trades so far
.\deploy\remote.ps1 kill         # KILL SWITCH: stop, keep coins
.\deploy\remote.ps1 killsell     # KILL SWITCH: stop and sell everything
.\deploy\remote.ps1 resume       # after a stop: review first, then start again
```

After changing code: commit, then `.\deploy\deploy.ps1`. State, logs and price data on the
server are kept.

**Heartbeat:** the daily report arrives around 07:00-08:00 Norwegian time. If it does not come,
check `status` - the server or the bot is down.

## What protects the server

- Login only with your SSH key (Ubuntu images on Oracle have password login turned off).
- Only port 22 open; fail2ban blocks repeated failed logins.
- Security updates install automatically (unattended-upgrades).
- The bot runs as its own user without login rights and can only write in `/opt/cryptobot`.
- `.env` is readable only by the bot user.
- A crash restarts the bot after a minute; a kill switch or loss-limit stop does **not** restart
  until you run `resume`.
