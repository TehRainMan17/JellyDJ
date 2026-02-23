"""
Lidarr diagnostic — copy to container and run:
  docker cp test_lidarr.py jellydj-backend:/app/test_lidarr.py
  docker exec jellydj-backend python3 /app/test_lidarr.py "Artist Name" "Album Name"
"""
import sys
import asyncio
import httpx

async def test(artist_name: str, album_name: str):
    import sys
    sys.path.insert(0, '/app')
    from database import SessionLocal
    from models import ConnectionSettings
    from crypto import decrypt

    db = SessionLocal()
    row = db.query(ConnectionSettings).filter_by(service="lidarr").first()
    if not row:
        print("ERROR: Lidarr not configured"); return
    base_url = row.base_url.rstrip("/")
    api_key = decrypt(row.api_key_encrypted)
    headers = {"X-Api-Key": api_key}
    print(f"Lidarr URL: {base_url}")

    async with httpx.AsyncClient(timeout=30) as client:
        # Find artist already in Lidarr
        existing_r = await client.get(f"{base_url}/api/v1/artist", headers=headers)
        existing = existing_r.json()
        lidarr_artist = next(
            (a for a in existing if artist_name.lower() in a.get("artistName","").lower()), None
        )
        if not lidarr_artist:
            print(f"'{artist_name}' not found in Lidarr. Add them first via the UI.")
            print(f"Artists in Lidarr: {[a['artistName'] for a in existing[:10]]}")
            return

        lidarr_id = lidarr_artist["id"]
        print(f"\nFound: {lidarr_artist['artistName']} (lidarr id={lidarr_id})")

        # Fetch albums
        print(f"\n--- Albums (artistId={lidarr_id}) ---")
        alb_r = await client.get(f"{base_url}/api/v1/album", headers=headers, params={"artistId": lidarr_id})
        print(f"HTTP {alb_r.status_code}")
        albums = alb_r.json()
        print(f"{len(albums)} albums total:")
        for a in albums[:8]:
            print(f"  id={a['id']} monitored={a['monitored']} title={repr(a['title'])}")

        if not albums:
            print("No albums — try triggering a refresh first:")
            ref = await client.post(f"{base_url}/api/v1/command", headers=headers,
                                    json={"name": "RefreshArtist", "artistId": lidarr_id})
            print(f"RefreshArtist: HTTP {ref.status_code} — {ref.text[:200]}")
            return

        target = album_name.lower()
        match = next((a for a in albums if target in a.get("title","").lower()), albums[0])
        print(f"\nTargeting: id={match['id']} title={repr(match['title'])}")

        # Monitor
        print("\n--- PUT /album (monitor=True) ---")
        match["monitored"] = True
        put_r = await client.put(f"{base_url}/api/v1/album/{match['id']}", headers=headers, json=match)
        print(f"HTTP {put_r.status_code} — {put_r.text[:300]}")

        # AlbumSearch
        print("\n--- POST /command AlbumSearch ---")
        cmd_r = await client.post(f"{base_url}/api/v1/command", headers=headers,
                                  json={"name": "AlbumSearch", "albumIds": [match["id"]]})
        print(f"HTTP {cmd_r.status_code} — {cmd_r.text[:400]}")

asyncio.run(test(
    sys.argv[1] if len(sys.argv) > 1 else "The Beatles",
    sys.argv[2] if len(sys.argv) > 2 else "Abbey Road",
))
