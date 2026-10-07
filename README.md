# Reaction Desk

A self-updating dashboard of healthcare news where tech meets business: funding and M&A, NEJM/JAMA/Nature Medicine/Lancet Digital Health papers, FDA and CMS moves, investor takes, and industry news. Every story shows how old it is so you can react while it's fresh.

It runs entirely on GitHub for free. Once an hour a GitHub Action pulls ~30 sources, scores and de-duplicates stories, and commits `data/items.json`. GitHub Pages serves the page, which re-checks for new data every 10 minutes.

## Set it up (about 5 minutes)

1. Create a new repository on GitHub (public is simplest; Pages on private repos needs a paid plan).
2. Upload everything in this folder, including the hidden `.github` folder and `.nojekyll`. Easiest way: on the empty repo page click "uploading an existing file" and drag the folder contents in. If the `.github` folder doesn't come through, create the file `.github/workflows/update.yml` by hand with **Add file → Create new file** and paste its contents.
3. **Settings → Actions → General → Workflow permissions**: choose "Read and write permissions" and save.
4. **Settings → Pages**: Source "Deploy from a branch", branch `main`, folder `/ (root)`. Save.
5. **Actions tab → Update desk → Run workflow** to fill it right away instead of waiting for the next hour.

Your dashboard lives at `https://<your-username>.github.io/<repo-name>/`.

## What updates, and how often

- The fetch runs every hour at :17 UTC. GitHub sometimes delays scheduled runs by 5 to 20 minutes.
- Google News queries cover the last 24 hours, so company announcements (funding, FDA clearances, deals) usually appear within an hour or two of publication.
- The FDA 510(k) database is official but lags several weeks; those entries show as "background".
- Stories are kept for 21 days after first being seen.
- If a source breaks, the run keeps going and the "Sources" panel at the bottom of the page shows it in red.

## Customize

- **Sources**: edit `sources.json`. Add any RSS feed or a Google News query and pick its lane (`deals`, `research`, `regulatory`, `takes`, `industry`). Saving the file triggers a fresh run.
- **Newsletters without RSS**: many beehiiv newsletters (Health Tech Nerds, Healthcare Huddle) have RSS switched off. Add them under `pages` in `sources.json` with their archive URL (usually `https://<site>/archive`); the script reads the post links from that page. If a site redesigns and the links stop matching, it shows as failed in the Sources panel.
- **Optional AI angles**: add a repository secret `ANTHROPIC_API_KEY` (Settings → Secrets and variables → Actions). The script then writes a one or two sentence angle for up to 10 hot new stories per run. Change the perspective with a repository variable `ANGLE_PERSPECTIVE`. Without the key, everything else works the same.
- **Posted / Skipped** marks are saved in your browser only.

## Heat score (1 to 5)

Higher when several outlets cover the same story, the deal is $50M+ (more for $250M+), it mentions AI or big names (Epic, Optum, OpenAI, GLP-1s…), it comes from a top journal or STAT, or it's dental. Lower once it's more than three days old.

## Keep it running

GitHub pauses scheduled workflows in repos with no activity for 60 days. The hourly data commits usually count as activity, but if the "Updated" time on the page goes stale, open the Actions tab and re-enable the workflow.
