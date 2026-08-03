# Using Druthers with Claude (MCP)

[Druthers](https://www.druthers.io) — track and rank the movies, TV shows,
books, and games you actually care about — has a
[Model Context Protocol](https://modelcontextprotocol.io) (MCP) server, so you
can talk to your library from **Claude Desktop** or **Claude Code** instead of
opening the site: *"add Dune to my watchlist," "mark episode 3 of Severance
watched," "what have I 100%'d this year?"*

This page is for **end users** connecting their own Druthers account to
Claude. If you're contributing to the server itself, see the
[druthers-mcp README](https://github.com/ALeonard9/druthers-mcp) instead.

## What you need

1. A [druthers.io](https://www.druthers.io) account.
2. A personal **API key** (looks like `drk_…`) — mint one at
   [www.druthers.io/settings](https://www.druthers.io/settings) → **API
   keys**, give it a name (e.g. "laptop mcp"), and click **Mint key**. Copy
   it now; it's shown once.
3. [Python 3.13+](https://www.python.org/downloads/) installed locally (the
   MCP server runs on your machine and talks to the Druthers API over HTTPS —
   your credentials never touch Anthropic's servers).
4. Claude Desktop or Claude Code.

## 1. Install the server

```bash
git clone https://github.com/ALeonard9/druthers-mcp.git
cd druthers-mcp
python3.13 -m venv .venv && source .venv/bin/activate
pip install -r requirements/dev.txt
```

## 2. Connect it to Claude

The server runs over stdio and reads its configuration from environment
variables — `API_BASE_URL` (the Druthers API) and `API_TOKEN` (your API key).

### Claude Code

```bash
claude mcp add druthers \
  -e API_BASE_URL=https://api.druthers.io \
  -e API_TOKEN=drk_your_key_here \
  --scope user \
  -- python -m aleonard_mcp.server
```

Verify it connected:

```bash
claude mcp get druthers   # expect "✔ Connected"
```

### Claude Desktop

Edit your MCP config file:

- **macOS:** `~/Library/Application Support/Claude/claude_desktop_config.json`
- **Windows:** `%APPDATA%\Claude\claude_desktop_config.json`

Add a `druthers` entry under `mcpServers` (merge with whatever's already
there):

```json
{
  "mcpServers": {
    "druthers": {
      "command": "/absolute/path/to/druthers-mcp/.venv/bin/python",
      "args": ["-m", "aleonard_mcp.server"],
      "env": {
        "API_BASE_URL": "https://api.druthers.io",
        "API_TOKEN": "drk_your_key_here"
      }
    }
  }
}
```

Restart Claude Desktop. You should see a small MCP/plug icon indicating
`druthers` is connected, and Claude will have access to tools named
`mcp__druthers__*` (or just `druthers` tools, depending on your client's UI).

> Keep your API key out of version control and don't share it — it acts as
> your Druthers login for API purposes. Revoke a leaked key any time from
> **Settings → API keys** and mint a fresh one.

## What the tools do

The server exposes a consistent set of verbs across every domain it tracks —
**movies, TV, books, games**:

| Verb pattern | Example | What it does |
|---|---|---|
| `search_*` | `search_movies("dune")` | Look up titles in the external catalog (OMDb/TMDB, TVmaze, Open Library, IGDB) to find the id you need to add something |
| `add_*` | `add_book(isbn, title)` | Add an item to your library (watchlist/backlog) |
| `list_my_*` | `list_my_games()` | List everything you're tracking in a domain, with status, rank, and notes |
| `*_detail` | `movie_detail(id)` | Full metadata for one item — plot, cast, genre, rating, etc. |
| `mark_*` | `mark_watched(id)` · `mark_episode_watched(id)` · `mark_game_100_percent(id)` | Flip a status (watched, episode watched, 100%-completed) |
| `set_*_note` | `set_note(id, "loved this")` | Set or replace your personal note on a tracked item |
| `set_*_completed_date` | `set_completed_date(id, "2026-03-01")` | Set (or clear) the date you finished something |

Full tool list by domain:

- **Movies:** `search_movies`, `list_my_movies`, `movie_detail`, `add_movie`,
  `mark_watched`, `set_note`, `set_completed_date`
- **TV:** `search_tv_shows`, `list_my_tv_shows`, `tv_show_detail`,
  `add_tv_show`, `set_tv_note`, `show_episodes`, `mark_episode_watched`,
  `set_tv_completed_date`
- **Books:** `search_books`, `list_my_books`, `book_detail`, `add_book`,
  `set_book_note`, `set_book_completed_date`
- **Games:** `search_games`, `list_my_games`, `game_detail`, `add_game`,
  `set_game_note`, `mark_game_100_percent`, `set_game_completed_date`

Every tool acts on **your** account — the API key you configured is the
authorization for all of it, so there's no separate per-request login step.

## Example prompts

Once connected, you can just talk to Claude naturally:

- *"Search for Dune 2021 and add it to my watchlist."*
- *"What's on my TV watchlist right now?"*
- *"Mark Severance season 1 episode 3 as watched."*
- *"Give Oppenheimer a note: 'best sound design I've heard in a theater.'"*
- *"What games have I 100%'d?"*
- *"I finished reading Project Hail Mary yesterday — mark it done."*

Claude will call the relevant tools, ask for confirmation on writes if it's
unsure which item you mean (there's often a `search_*` step first to resolve
a title to an id), and report back in plain language.

## Troubleshooting

- **Not connecting:** confirm the `python` path in your config is the venv's
  interpreter (`.venv/bin/python`), not a system Python missing the
  dependencies from `pip install -r requirements/dev.txt`.
- **401 / auth errors:** your `API_TOKEN` is wrong, expired, or was revoked —
  mint a new one at **Settings → API keys**.
- **503 on a `search_*` call:** that catalog's external API key isn't
  configured on the server side (rare — this is a Druthers-side config
  issue, not yours).
- **Pointing at a different environment:** set `API_BASE_URL` to
  `http://127.0.0.1:8000` for a local `druthers-api` checkout, or leave it at
  `https://api.druthers.io` for production.

## Related

- [Postman collection](./druthers-api.postman_collection.json) — call the
  same API directly over HTTP, outside of Claude.
- [druthers-mcp source](https://github.com/ALeonard9/druthers-mcp)
- [druthers-api source](https://github.com/ALeonard9/druthers-api) (this repo)
