from src.social_media.instagram.client import login_from_config
from src.social_media.instagram.profile import InstagramProfile

username = input("Instagram username to look up: ").strip()

client = login_from_config(session_file="ig_session.json")
profile = InstagramProfile(client, username)

print(f"{profile.username} has {profile.follower_count} followers")
