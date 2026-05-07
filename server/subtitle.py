import re


def convert_srt_to_vtt(srt_path: str, vtt_path: str):
    with open(srt_path, "r", encoding="utf-8-sig") as f:
        content = f.read()

    content = content.replace("\r\n", "\n")

    def replace_timestamp(match):
        s = match.group(0)
        return s.replace(",", ".")

    content = re.sub(
        r"\d{2}:\d{2}:\d{2},\d{3}\s*-->\s*\d{2}:\d{2}:\d{2},\d{3}",
        replace_timestamp,
        content,
    )

    content = "WEBVTT\n\n" + content

    with open(vtt_path, "w", encoding="utf-8") as f:
        f.write(content)
