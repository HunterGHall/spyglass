def one_site(site):
    return f"site:{site}"

def filetype(filetype):
    return f"filetype:{filetype}"

def inurl(url):
    return f"inurl:{url}"

def intitle(title):
    return f"intitle:{title}"

def intext(text):
    return f"intext:{text}"

def exact_phrase(phrase):
    return f"\"{phrase}\""

def exclude(phrase):
    if " " in phrase:
        return f"-\"{phrase}\""
    else:
        return f"-{phrase}"

def either_term():
    return " OR "

def before(date):
    return f"before:{date}"   # YYYY-MM-DD

def after(date):
    return f"after:{date}"







# functionality:
def build_query(*parts):
    return " ".join(p for p in parts if p)

if __name__ == "__main__":
    q = build_query(
        exact_phrase("hot dog"),
        one_site("wikipedia.org")
    )

    print(q)