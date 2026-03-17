from fastapi import FastAPI, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
import fitz  # PyMuPDF
import base64
import binascii
import html
import re

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def string_to_hex(s: str) -> str:
    return s.encode('utf-8').hex()

def get_html_from_page(page) -> str:
    # get_text("html") produces an exact visual replica of the source PDF using absolute-positioned CSS
    raw_html = page.get_text("html")
    # PyMuPDF outputs standard HTML5 without self-closing un-paired tags like img or br.
    # To satisfy strict XHTML parsers in browsers for the .xhtml files, we automatically self-close them.
    raw_html = re.sub(r'<img([^>]*?)(?<!/)>', r'<img\1/>', raw_html)
    raw_html = re.sub(r'<br([^>]*?)(?<!/)>', r'<br\1/>', raw_html)
    return raw_html

@app.post("/upload/")
async def upload_pdf(file: UploadFile = File(...)):
    if not file.filename.endswith('.pdf'):
        return JSONResponse(status_code=400, content={"error": "File must be a PDF."})
    
    contents = await file.read()
    try:
        doc = fitz.open(stream=contents, filetype="pdf")
    except Exception as e:
        return JSONResponse(status_code=400, content={"error": f"Failed to open PDF: {str(e)}"})

    toc = doc.get_toc() # [[level, title, page_number], ...]
    
    # If no TOC, we will treat each page as a chapter, or the whole thing as one chapter.
    chapters = []
    if toc:
        for i, item in enumerate(toc):
            level, title, page_num = item
            start_page = page_num - 1
            if i + 1 < len(toc):
                end_page = toc[i+1][2] - 1
            else:
                end_page = doc.page_count
            
            chapter_text = ""
            for p in range(start_page, end_page):
                if p < doc.page_count:
                    page = doc.load_page(p)
                    chapter_text += get_html_from_page(page)
                
            chapters.append({
                "title": title,
                "text": chapter_text,
                "hex": string_to_hex(chapter_text)
            })
    else:
        # Fallback if no logical chapters exist
        full_text = ""
        for page in doc:
            full_text += get_html_from_page(page)
        chapters.append({
            "title": "Document Content",
            "text": full_text,
            "hex": string_to_hex(full_text)
        })

    # Extract all images, charts, and vector graphics
    images = []
    for i in range(doc.page_count):
        page = doc.load_page(i)
        
        # Layout analysis: type 1 blocks identify visual components (images, charts, graphs)
        blocks = page.get_text("blocks")
        img_blocks = [b for b in blocks if b[6] == 1]
        
        for img_index, b in enumerate(img_blocks):
            bbox = fitz.Rect(b[:4])
            # Filter out tiny insignificant artifact blocks
            if bbox.width > 20 and bbox.height > 20:
                try:
                    # Render the chart/image area from the page at 300 DPI for high quality
                    pix = page.get_pixmap(clip=bbox, dpi=300)
                    image_bytes = pix.tobytes("png")
                    img_b64 = base64.b64encode(image_bytes).decode('utf-8')
                    images.append({
                        "page": i + 1,
                        "ext": "png",
                        "data": img_b64
                    })
                except Exception:
                    pass
                    
    doc.close()
    
    return {
        "chapters": chapters,
        "images": images,
        "filename": file.filename
    }
