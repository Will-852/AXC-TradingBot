# Gamma API — Market Discovery & Metadata

> Base URL: `https://gamma-api.polymarket.com`
> Auth: None (all public)

---

## Events

### GET /events — List Events
**Query params** (all optional):
| Param | Type | Description |
|-------|------|-------------|
| `limit` | int | Pagination limit |
| `offset` | int | Pagination offset |
| `order` | string | Sort field |
| `ascending` | bool | Sort direction |
| `id` | int[] | Filter by IDs |
| `slug` | string[] | Filter by slugs |
| `tag_id` | int | Filter by tag |
| `exclude_tag_id` | int[] | Exclude tags |
| `tag_slug` | string | Filter by tag slug |
| `related_tags` | bool | Include related tags |
| `active` | bool | Active only |
| `archived` | bool | Archived only |
| `featured` | bool | Featured only |
| `cyom` | bool | CYOM (create your own market) |
| `closed` | bool | Closed only |
| `include_chat` | bool | Include chat data |
| `include_template` | bool | Include template |
| `recurrence` | string | Recurrence filter |
| `liquidity_min` / `liquidity_max` | number | Liquidity range |
| `volume_min` / `volume_max` | number | Volume range |
| `start_date_min` / `start_date_max` | datetime | Start date range |
| `end_date_min` / `end_date_max` | datetime | End date range |

**Response**: Array of Event objects

### GET /events/{id} — Single Event
**Path**: `id` (int, required)
**Query**: `include_chat`, `include_template`
**Response**: Event object | 404

### GET /events/slug/{slug} — Event by Slug
**Path**: `slug` (string, required)
**Query**: `include_chat`, `include_template`
**Response**: Event object | 404

### GET /events/{id}/tags — Event Tags
**Path**: `id` (int, required)
**Response**: Tag[] | 404

---

## Markets

### GET /markets — List Markets
**Query params** (all optional):
| Param | Type | Description |
|-------|------|-------------|
| `limit`, `offset`, `order`, `ascending` | — | Pagination/sort |
| `id` | int[] | Filter by IDs |
| `slug` | string[] | Filter by slugs |
| `clob_token_ids` | string[] | Filter by CLOB token IDs |
| `condition_ids` | string[] | Filter by condition IDs |
| `market_maker_address` | string[] | Filter by MM address |
| `liquidity_num_min` / `max` | number | Liquidity range |
| `volume_num_min` / `max` | number | Volume range |
| `start_date_min` / `max` | datetime | Start date range |
| `end_date_min` / `max` | datetime | End date range |
| `tag_id` | int | Tag filter |
| `related_tags` | bool | Include related |
| `cyom` | bool | CYOM filter |
| `uma_resolution_status` | string | Resolution status |
| `game_id` | string | Game ID (sports) |
| `sports_market_types` | string[] | Sports market types |
| `rewards_min_size` | number | Min reward size |
| `question_ids` | string[] | Question ID filter |
| `include_tag` | bool | Include tag data |
| `closed` | bool | Closed filter |

**Response**: Market[]

### GET /markets/{id} — Single Market
**Path**: `id` (int, required)
**Query**: `include_tag`
**Response**: Market object | 404

### GET /markets/slug/{slug} — Market by Slug
**Path**: `slug` (string, required)
**Query**: `include_tag`
**Response**: Market object | 404

### GET /markets/{id}/tags — Market Tags
**Path**: `id` (int, required)
**Response**: Tag[] | 404

### POST /markets/information — Batch Query Markets
**Body** (MarketsInformationBody):
```json
{
  "id": [1, 2],
  "slug": ["market-slug"],
  "closed": false,
  "clobTokenIds": ["token_id"],
  "conditionIds": ["0x..."],
  "liquidityNumMin": 1000,
  "volumeNumMin": 5000,
  "tagId": 5,
  "relatedTags": true
}
```
**Response**: Market[] | 422

### POST /markets/abridged — Batch Query (Abridged)
Same body as `/markets/information`.
**Response**: Abridged Market[]

---

## Tags

### GET /tags — List Tags
**Query**: `limit`, `offset`, `order`, `ascending`, `include_template`, `is_carousel`
**Response**: Tag[]

### GET /tags/{id} — Single Tag
**Path**: `id` (int, required)
**Query**: `include_template`
**Response**: Tag | 404

### GET /tags/slug/{slug} — Tag by Slug
**Path**: `slug` (string, required)
**Response**: Tag | 404

### GET /tags/{id}/related-tags — Related Tag Relationships
**Path**: `id` (int, required)
**Query**: `omit_empty` (bool), `status` (enum: active, closed, all)
**Response**: RelatedTag[]

### GET /tags/slug/{slug}/related-tags — Related Tags (by slug)
Same as above but path is `slug`.

### GET /tags/{id}/related-tags/tags — Tags Related to Tag
**Path**: `id` (int, required)
**Query**: `omit_empty`, `status`
**Response**: Tag[]

### GET /tags/slug/{slug}/related-tags/tags — Related Tags List (by slug)
Same params. **Response**: Tag[]

---

## Series

### GET /series — List Series
**Query**: `limit`, `offset`, `order`, `ascending`, `slug` (string[]), `categories_ids` (int[]), `categories_labels` (string[]), `closed`, `include_chat`, `recurrence`, `exclude_events`
**Response**: Series[]

### GET /series/{id} — Single Series
**Path**: `id` (int, required)
**Query**: `include_chat`
**Response**: Series | 404

---

## Comments

### GET /comments — List Comments
**Query**: `limit`, `offset`, `order`, `ascending`, `parent_entity_type` (enum: Event, Series, market), `parent_entity_id` (int), `get_positions`, `holders_only`
**Response**: Comment[]

### GET /comments/{id} — Comment Thread
**Path**: `id` (int, required)
**Query**: `get_positions`
**Response**: Comment[] (thread)

### GET /comments/user_address/{user_address} — User Comments
**Path**: `user_address` (string, required)
**Query**: `limit`, `offset`, `order`, `ascending`
**Response**: Comment[]

---

## Profiles

### GET /public-profile — Public Profile
**Query**: `address` (string, required, pattern: `^0x[a-fA-F0-9]{40}$`)
**Response**: PublicProfileResponse
```json
{
  "createdAt": "2024-01-01T00:00:00Z",
  "proxyWallet": "0x...",
  "profileImage": "https://...",
  "displayUsernamePublic": true,
  "bio": "...",
  "pseudonym": "Trader123",
  "name": "John",
  "xUsername": "john_x",
  "verifiedBadge": false,
  "users": [{"id": 1, "creator": false, "mod": false}]
}
```
**Errors**: 400 (invalid address), 404 (not found)

---

## Sports

### GET /teams — List Teams
**Query**: `limit`, `offset`, `order`, `ascending`, `league` (string[]), `name` (string[]), `abbreviation` (string[])
**Response**: Team[]
```json
{"id": 1, "name": "Lakers", "league": "NBA", "record": "40-20", "logo": "https://...", "abbreviation": "LAL", "alias": "Los Angeles Lakers"}
```

### GET /sports — Sports Metadata
No params. Returns config objects with sport, image, resolution, ordering, tags, series.

### GET /sports/market-types — Valid Sports Market Types
No params. **Response**: `{marketTypes: string[]}`

---

## Search

### GET /public-search — Full-text Search
**Query**:
| Param | Type | Required | Description |
|-------|------|----------|-------------|
| `q` | string | **Yes** | Search query |
| `cache` | bool | No | Use cache |
| `events_status` | string | No | Event status filter |
| `limit_per_type` | int | No | Results per type |
| `page` | int | No | Pagination |
| `events_tag` | string[] | No | Tag filter |
| `keep_closed_markets` | int | No | Include closed |
| `sort` | string | No | Sort field |
| `ascending` | bool | No | Sort direction |
| `search_tags` | bool | No | Include tags in results |
| `search_profiles` | bool | No | Include profiles |
| `recurrence` | string | No | Recurrence filter |
| `exclude_tag_id` | int[] | No | Exclude tags |
| `optimized` | bool | No | Optimized search |

**Response**:
```json
{
  "events": [...],
  "tags": [...],
  "profiles": [...],
  "pagination": {...}
}
```

---

## Health

### GET /status
No params. Returns `"OK"`.
