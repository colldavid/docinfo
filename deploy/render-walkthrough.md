# Deploying DocInfo to Render (share a link with your team)

This gives you a public URL your teammates can open in any browser — no install,
no API key on their end. You do this once; they just click the link.

Estimated time: ~15 minutes. Cost: free tier works for a demo, but see notes at the bottom.

---

## Prerequisites (one-time)

1. **A GitHub account**, with this project pushed to a GitHub repository.
   - If it's not on GitHub yet: create a new repo at github.com, then in your project
     folder run `git push`. (The repo can be private — Render can still access it.)
2. **A Render account** — sign up free at https://render.com (log in with GitHub, easiest).
3. **Your Anthropic API key** — the one in your local `.env.local` file
   (starts with `sk-ant-...`). You'll paste it into Render, not into the code.

---

## Deploy steps

1. Push the latest code (including `render.yaml` and the `Dockerfile`) to GitHub.

2. In the Render dashboard, click **New → Blueprint**.

3. Connect this GitHub repository. Render detects `render.yaml` automatically and
   shows one service called **docinfo**.

4. It will prompt you for the **ANTHROPIC_API_KEY** value. Paste your key. Click **Apply**.

5. Render builds the Docker image (frontend + backend). First build takes ~5–10 minutes
   because it installs dependencies and downloads the embedding model. Watch the log;
   when it says **"Live"**, you're done.

6. You get a URL like `https://docinfo.onrender.com`. Open it — that's the app.
   Share that link with your team.

---

## Sharing with teammates

Just send them the URL. They open it in Chrome/Safari/Edge on any laptop or phone.
Nothing to install. Every classification they run uses **your** API key (see cost note).

---

## Cost & safety notes

- **Render free tier** spins the service down after ~15 min of inactivity; the next
  visitor waits ~30–60 sec for it to wake up. Fine for a casual demo. The **Starter**
  plan (~$7/month) keeps it always-on and gives more memory.
- **Memory:** the embedding model needs a fair bit of RAM. If the free/starter instance
  runs out of memory (build succeeds but the app crashes on first classify), bump the
  `plan:` in `render.yaml` from `starter` to `standard` and redeploy.
- **API spend is yours.** Every teammate's classification bills your Anthropic account.
  For a small trial this is a few dollars at most, but set a spend limit in the
  Anthropic console (Billing → Usage limits) before sharing widely, so a runaway batch
  can't surprise you.
- **To take it offline:** in Render, suspend or delete the service. The link stops working;
  no further cost.

---

## Updating the app later

Any time you `git push` to the connected branch, Render automatically rebuilds and
redeploys. No manual step.
