import os
import re
import urllib.request
import hashlib

MANUAL_DIR = "/home/pi/Desktop/core/new/docs/manual"
IMG_DIR = os.path.join(MANUAL_DIR, "images")

if not os.path.exists(IMG_DIR):
    os.makedirs(IMG_DIR)

def download_image(url):
    try:
        # MD5 hash for filename
        url_hash = hashlib.md5(url.encode('utf-8')).hexdigest()[:8]
        # Try to extract extension from URL, default to .jpg
        ext = ".jpg"
        if "." in url.split("/")[-1]:
            possible_ext = url.split("/")[-1].split(".")[1]
            if len(possible_ext) <= 4:
                ext = "." + possible_ext
        
        filename = f"img_{url_hash}{ext}"
        filepath = os.path.join(IMG_DIR, filename)
        
        if not os.path.exists(filepath):
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req) as response, open(filepath, 'wb') as out_file:
                data = response.read()
                out_file.write(data)
                
        return f"images/{filename}"
    except Exception as e:
        print(f"Error downloading {url}: {e}")
        return url

def process_file(filepath):
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()

    # 1. Download and replace images
    def image_replacer(match):
        alt_text = match.group(1)
        url = match.group(2)
        if url.startswith("http"):
            local_path = download_image(url)
            return f"![{alt_text}]({local_path})"
        return match.group(0)

    content = re.sub(r'!\[(.*?)\]\((http[s]?://.*?)\)', image_replacer, content)

    # 2. Fix OCR bullets (, , $\bullet$, $\diamondsuit$) -> -
    # Match these at the start of a line (with optional spaces)
    content = re.sub(r'^[ \t]*[][ \t]*', r'- ', content, flags=re.MULTILINE)
    content = re.sub(r'^[ \t]*\$\\bullet\$[ \t]*', r'- ', content, flags=re.MULTILINE)
    content = re.sub(r'^[ \t]*\$\\diamondsuit\$[ \t]*', r'- ', content, flags=re.MULTILINE)

    # 3. Fix OCR LaTeX circled numbers $\textcircled{N}$ -> (N)
    content = re.sub(r'\$\\textcircled\{([0-9]+)\}\$', r'(\1)', content)

    # 4. Fix Headers like "# 1、xxx" -> "# 1. xxx"
    content = re.sub(r'^(#+)\s*([0-9]+)[、，。]\s*', r'\1 \2. ', content, flags=re.MULTILINE)

    # 5. Remove excessive multiple empty lines (replace 3+ newlines with 2 newlines)
    content = re.sub(r'\n{3,}', r'\n\n', content)

    with open(filepath, 'w', encoding='utf-8') as f:
        f.write(content)

def main():
    for filename in os.listdir(MANUAL_DIR):
        if filename.endswith(".md"):
            filepath = os.path.join(MANUAL_DIR, filename)
            print(f"Processing {filename}...")
            process_file(filepath)
            
    print("All files processed!")

if __name__ == "__main__":
    main()
