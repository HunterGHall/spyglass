from instagrapi import Client

from .post import InstagramPost


class InstagramProfile:
    """Wraps an Instagram account, fetched via an authenticated instagrapi Client."""

    def __init__(self, client: Client, username: str, fetch: bool = True):
        self.client = client
        self.username = username
        self.user_id = None
        self._info = None
        if fetch:
            self.fetch()

    def fetch(self):
        """Fetch (or re-fetch) the account's profile info."""
        self.user_id = self.client.user_id_from_username(self.username)
        self._info = self.client.user_info(self.user_id)
        return self

    @property
    def info(self):
        if self._info is None:
            self.fetch()
        return self._info

    @property
    def full_name(self): return self.info.full_name

    @property
    def biography(self): return self.info.biography

    @property
    def follower_count(self): return self.info.follower_count

    @property
    def following_count(self): return self.info.following_count

    @property
    def media_count(self): return self.info.media_count

    @property
    def is_private(self): return self.info.is_private

    @property
    def is_verified(self): return self.info.is_verified

    @property
    def external_url(self): return self.info.external_url

    @property
    def category(self): return self.info.category

    @property
    def profile_pic_url(self):
        url = self.info.profile_pic_url_hd or self.info.profile_pic_url
        return str(url) if url else None

    def recent_posts(self, amount: int = 12) -> list[InstagramPost]:
        """Fetch the account's most recent posts as InstagramPost objects."""
        medias = self.client.user_medias(self.user_id, amount)
        return [InstagramPost.from_media(self.client, media) for media in medias]

    def to_dict(self):
        fields = [
            "user_id", "username", "full_name", "biography", "follower_count",
            "following_count", "media_count", "is_private", "is_verified",
            "external_url", "category", "profile_pic_url",
        ]
        return {field: getattr(self, field) for field in fields}


if __name__ == "__main__":
    from .client import login_from_config

    ig_client = login_from_config(session_file="ig_session.json")
    profile = InstagramProfile(ig_client, "instagram")
    print(profile.full_name)
    print(profile.follower_count)
    print(profile.biography)
