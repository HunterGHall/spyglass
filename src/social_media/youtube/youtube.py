import re
import requests


class YouTubeVideo:
    _PATTERNS = {
        "views": r'"viewCount":"(\d+)"',
        "length_seconds": r'"lengthSeconds":"(\d+)"',
        "title": r'"title":"(.*?)"',
        "channel_id": r'"channelId":"(.*?)"',
        "author": r'"author":"(.*?)"',
        "publish_date": r'"publishDate":"(.*?)"',
        "upload_date": r'"uploadDate":"(.*?)"',
        "description": r'"shortDescription":"(.*?)"',
        "keywords": r'<meta name="keywords" content="(.*?)">',
        "category": r'"category":"(.*?)"',
        "is_live_content": r'"isLiveContent":(true|false)',
        "allow_ratings": r'"allowRatings":(true|false)',
        "is_private": r'"isPrivate":(true|false)',
        "is_unlisted": r'"isUnlisted":(true|false)',
        "is_crawlable": r'"isCrawlable":(true|false)',
        "thumbnails_raw": r'"thumbnail":\{"thumbnails":(\[.*?\])\}',
        "caption_url": r'"baseUrl":"(https://www\.youtube\.com/api/timedtext.*?)"',
        "subscriber_count": r'"subscriberCountText":\{"accessibility":\{"accessibilityData":\{"label":".*?"\}\},"simpleText":"(.*?)"\}',
        "like_count": r'itemprop="userInteractionCount" content="(\d+)"',
    }

    def __init__(self, video_id, fetch=True):
        self.video_id = video_id
        self.url = f"https://www.youtube.com/watch?v={video_id}"
        self._html = None
        self._cache = {}
        if fetch:
            self.fetch()

    def fetch(self):
        """Fetch (or re-fetch) the page HTML and clear cached fields."""
        self._html = requests.get(self.url).text
        self._cache.clear()
        return self

    @property
    def html(self):
        if self._html is None:
            self.fetch()
        return self._html

    def _extract(self, key):
        if key not in self._cache:
            pattern = self._PATTERNS[key]
            match = re.compile(pattern).search(self.html)
            self._cache[key] = match.group(1) if match else None
        return self._cache[key]

    @property
    def views(self): return self._extract("views")

    @property
    def length_seconds(self): return self._extract("length_seconds")

    @property
    def title(self): return self._extract("title")

    @property
    def channel_id(self): return self._extract("channel_id")

    @property
    def author(self): return self._extract("author")

    @property
    def publish_date(self): return self._extract("publish_date")

    @property
    def upload_date(self): return self._extract("upload_date")

    @property
    def description(self): return self._extract("description")

    @property
    def keywords(self): return self._extract("keywords")

    @property
    def category(self): return self._extract("category")

    @property
    def is_live_content(self): return self._extract("is_live_content")

    @property
    def allow_ratings(self): return self._extract("allow_ratings")

    @property
    def is_private(self): return self._extract("is_private")

    @property
    def is_unlisted(self): return self._extract("is_unlisted")

    @property
    def is_crawlable(self): return self._extract("is_crawlable")

    @property
    def thumbnails(self): return self._extract("thumbnails_raw")

    @property
    def caption_url(self): return self._extract("caption_url")

    @property
    def subscriber_count(self): return self._extract("subscriber_count")

    @property
    def like_count(self): return self._extract("like_count")

    def to_dict(self):
        fields = [
            "views", "length_seconds", "title", "channel_id", "author",
            "publish_date", "upload_date", "description", "keywords",
            "category", "is_live_content", "allow_ratings", "is_private",
            "is_unlisted", "is_crawlable", "thumbnails", "caption_url",
            "subscriber_count", "like_count",
        ]
        return {field: getattr(self, field) for field in fields}


if __name__ == "__main__":
    video = YouTubeVideo("VqCEDcvhfOM")
    print(video.title)
    print(video.views)
    print(video.author)
    print(video.like_count)
    print(video.url)