import sys
import urllib.request
import re

def fetch_markdown(url):
    # Sử dụng Jina Reader API để chuyển đổi trang web bất kỳ sang Markdown sạch
    jina_url = f"https://r.jina.ai/{url}"
    req = urllib.request.Request(
        jina_url, 
        headers={'User-Agent': 'Mozilla/5.0'}
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as response:
            content = response.read().decode('utf-8')
            print(content)
    except Exception as e:
        print(f"Error fetching URL: {e}", file=sys.stderr)

if __name__ == "__main__":
    if len(sys.argv) > 1:
        fetch_markdown(sys.argv[1])