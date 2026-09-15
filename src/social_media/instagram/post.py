from instagrapi import Client


class InstagramPost:
    """Wraps a single Instagram post (media), fetched via an authenticated
    instagrapi Client."""

    def __init__(self, client: Client, shortcode: str = None, fetch: bool = True):
        if shortcode is None and fetch:
            raise ValueError("shortcode is required to fetch a post")

        self.client = client
        self.shortcode = shortcode
        self.url = f"https://www.instagram.com/p/{shortcode}/" if shortcode else None
        self.media_pk = None
        self._info = None
        if fetch:
            self.fetch()

    @classmethod
    def from_media(cls, client: Client, media) -> "InstagramPost":
        """Wrap an already-fetched instagrapi Media object without hitting
        the API again (used by InstagramProfile.recent_posts)."""
        post = cls(client, fetch=False)
        post.media_pk = media.pk
        post.shortcode = media.code
        post.url = f"https://www.instagram.com/p/{media.code}/"
        post._info = media
        return post

    def fetch(self):
        """Fetch (or re-fetch) the post's info."""
        self.media_pk = self.client.media_pk_from_code(self.shortcode)
        self._info = self.client.media_info(self.media_pk)
        return self

    @property
    def info(self):
        if self._info is None:
            self.fetch()
        return self._info

    @property
    def caption_text(self): return self.info.caption_text

    @property
    def like_count(self): return self.info.like_count

    @property
    def comment_count(self): return self.info.comment_count

    @property
    def view_count(self): return getattr(self.info, "view_count", None)

    @property
    def taken_at(self): return self.info.taken_at

    @property
    def media_type(self): return self.info.media_type

    @property
    def is_video(self): return self.media_type == 2

    @property
    def video_url(self):
        url = getattr(self.info, "video_url", None)
        return str(url) if url else None

    @property
    def thumbnail_url(self):
        url = getattr(self.info, "thumbnail_url", None)
        return str(url) if url else None

    @property
    def location(self): return self.info.location

    @property
    def author_username(self): return self.info.user.username

    def to_dict(self):
        fields = [
            "shortcode", "url", "caption_text", "like_count", "comment_count",
            "view_count", "taken_at", "media_type", "is_video", "video_url",
            "thumbnail_url", "location", "author_username",
        ]
        return {field: getattr(self, field) for field in fields}


if __name__ == "__main__":
    from .client import login_from_config

    ig_client = login_from_config(session_file="ig_session.json")
    post = InstagramPost(ig_client, "Cxxxxxxxxxx")
    print(post.caption_text)
    print(post.like_count)
    print(post.author_username)
