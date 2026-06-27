import os
import shutil
import tempfile
import zipfile
from urllib.parse import urlparse, parse_qs, urlencode, urlunparse

import requests
from fastapi import FastAPI, BackgroundTasks
from pydantic import BaseModel

app = FastAPI()

ALLOWED_EXTENSIONS = {
    ".pdf", ".jpg", ".jpeg", ".png", ".doc", ".docx"
}


class ExtractRequest(BaseModel):
    dropboxUrl: str
    callbackUrl: str
    runId: str
    gmailMessageId: str


def force_dropbox_download(url: str) -> str:
    parsed = urlparse(url)
    query = parse_qs(parsed.query)
    query["dl"] = ["1"]

    return urlunparse((
        parsed.scheme,
        parsed.netloc,
        parsed.path,
        parsed.params,
        urlencode(query, doseq=True),
        parsed.fragment
    ))


def process_zip(req: ExtractRequest):
    work_dir = tempfile.mkdtemp(prefix="dropbox_extract_")
    zip_path = os.path.join(work_dir, "dropbox_folder.zip")

    try:
        download_url = force_dropbox_download(req.dropboxUrl)

        print("Downloading Dropbox ZIP...")
        with requests.get(download_url, stream=True, timeout=600) as r:
            r.raise_for_status()
            with open(zip_path, "wb") as f:
                for chunk in r.iter_content(chunk_size=1024 * 1024):
                    if chunk:
                        f.write(chunk)

        print("ZIP downloaded. Reading entries...")

        # ---------- First pass: count eligible files ----------
        total_files = 0
        
        with zipfile.ZipFile(zip_path, "r") as z:
            for info in z.infolist():
        
                original_path = info.filename.replace("\\", "/")
        
                if info.is_dir() or original_path.endswith("/"):
                    continue
        
                file_name = os.path.basename(original_path)
        
                if not file_name or file_name.startswith("."):
                    continue
        
                ext = os.path.splitext(file_name)[1].lower()
        
                if ext not in ALLOWED_EXTENSIONS:
                    continue
        
                if info.file_size > 25 * 1024 * 1024:
                    continue
        
                total_files += 1
        
        print(f"Eligible files to send: {total_files}")
        
        # ---------- Second pass: send files ----------
        sent_count = 0
        total_entries = 0
        skipped_entries = 0

        with zipfile.ZipFile(zip_path, "r") as z:
            for info in z.infolist():
                total_entries += 1

                original_path = info.filename.replace("\\", "/")
                print("ZIP ENTRY:", original_path)

                if info.is_dir() or original_path.endswith("/"):
                    skipped_entries += 1
                    continue

                file_name = os.path.basename(original_path)

                if not file_name or file_name.startswith("."):
                    skipped_entries += 1
                    continue

                ext = os.path.splitext(file_name)[1].lower()

                if ext not in ALLOWED_EXTENSIONS:
                    print("SKIPPED EXT:", original_path)
                    skipped_entries += 1
                    continue

                file_size = info.file_size

                if file_size > 25 * 1024 * 1024:
                    print(f"SKIPPED LARGE FILE: {original_path} ({file_size} bytes)")
                    skipped_entries += 1
                    continue
                sent_count += 1
                content_id = f"dropbox_{sent_count:03d}"

                temp_file_path = os.path.join(work_dir, f"{sent_count}_{file_name}")

                with z.open(info) as source, open(temp_file_path, "wb") as target:
                    shutil.copyfileobj(source, target)

                with open(temp_file_path, "rb") as f:
                    response = requests.post(
                        req.callbackUrl,
                        data={
                            "runId": req.runId,
                            "gmailMessageId": req.gmailMessageId,
                            "contentId": content_id,
                            "fileName": file_name,
                            "originalPath": original_path,
                            "sourceType": "dropbox",
                            "totalFiles": total_files
                        },
                        files={
                            "file": (file_name, f)
                        },
                        timeout=600
                    )

                response.raise_for_status()
                print("SENT:", original_path)

        print(f"Finished. Total entries: {total_entries}, sent: {sent_count}, skipped: {skipped_entries}")

    except Exception as e:
        print(f"ERROR during Dropbox extraction: {e}")

    finally:
        shutil.rmtree(work_dir, ignore_errors=True)


@app.post("/extract-dropbox")
def extract_dropbox(req: ExtractRequest, background_tasks: BackgroundTasks):
    background_tasks.add_task(process_zip, req)

    return {
        "success": True,
        "message": "Dropbox extraction started",
        "runId": req.runId,
        "gmailMessageId": req.gmailMessageId
    }


@app.get("/")
def health_check():
    return {
        "status": "ok"
    }
