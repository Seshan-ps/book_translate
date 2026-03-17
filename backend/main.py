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

def get_html_from_page(page, page_num: int, images_list: list) -> str:
    # Use detailed dictionary extraction to access font sizes and styles
    blocks = page.get_text("dict")["blocks"]
    
    # Pre-calculate margins from text blocks
    text_blocks = [b for b in blocks if b["type"] == 0]
    left_margin = 9999
    for b in text_blocks:
        for l in b.get("lines", []):
            for s in l.get("spans", []):
                if s["text"].strip() and s["bbox"][0] < left_margin:
                    left_margin = s["bbox"][0]
    if left_margin == 9999:
        left_margin = 0
            
    # Sort blocks approximately top-to-bottom, left-to-right
    blocks = sorted(blocks, key=lambda b: (b["bbox"][1], b["bbox"][0]))
    
    in_list = False
    html_content = ""
    
    for b in blocks:
        if b["type"] == 0: # text
            text = ""
            max_font_size = 0
            is_bold = False
            first_char_x = 9999
            
            for line in b.get("lines", []):
                for span in line.get("spans", []):
                    span_text = span.get("text", "")
                    if not span_text.strip() and not text:
                        continue
                    text += span_text
                    font_sz = span.get("size", 0)
                    if font_sz > max_font_size:
                        max_font_size = font_sz
                    flags = span.get("flags", 0)
                    # Simple heuristic: bold flag in PyMuPDF is 2^4
                    if "Bold" in span.get("font", "") or (flags & 16):
                        is_bold = True
                    if first_char_x == 9999:
                        first_char_x = span["bbox"][0]
            
            text = text.strip()
            if not text:
                continue
                
            x0 = first_char_x
            is_indented = (x0 - left_margin) > 15
            class_name = "indent" if is_indented else "noindent"
            escaped_text = html.escape(text)
            
            # Simple content heuristic mapping to the specific classes requested by the user:
            
            # Very large fonts -> h1 chapter title
            if max_font_size > 18:
                if in_list: html_content += '</ul>\n'; in_list = False
                html_content += f'<header>\n<h1 class="h1c">{escaped_text}</h1>\n</header>\n'
                continue
                
            # Headers -> h2
            if max_font_size > 13 and is_bold:
                if in_list: html_content += '</ul>\n'; in_list = False
                html_content += f'<h2 class="content-area">{escaped_text}</h2>\n'
                continue
                
            # Bullets
            if text.startswith('•') or text.startswith('-'):
                if not in_list:
                    html_content += '<ul class="bullet">\n'
                    in_list = True
                cleaned = escaped_text.lstrip('•-').strip()
                html_content += f'<li class="bullt">{cleaned}</li>\n'
                continue
            
            if in_list:
                html_content += '</ul>\n'
                in_list = False
                
            html_content += f'<p class="{class_name}">{escaped_text}</p>\n'
            
        elif b["type"] == 1: # image
            bbox = fitz.Rect(b["bbox"])
            if bbox.width > 20 and bbox.height > 20:
                try:
                    pix = page.get_pixmap(clip=bbox, dpi=300)
                    image_bytes = pix.tobytes("png")
                    img_b64 = base64.b64encode(image_bytes).decode('utf-8')
                    img_idx = len(images_list) + 1
                    
                    images_list.append({
                        "page": page_num,
                        "idx": img_idx,
                        "ext": "png",
                        "data": img_b64
                    })
                    
                    # Inject standard XHTML <img> tag referencing the export path wrapped in a section/figure
                    html_content += f'<section class="center">\n<img alt="image" src="../images/image_page{page_num}_{img_idx}.png"/>\n</section>\n'
                except Exception:
                    pass
                    
    if in_list:
        html_content += '</ul>\n'
        
    return html_content

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
    images = []
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
                    chapter_text += get_html_from_page(page, p + 1, images)
                
            chapters.append({
                "title": title,
                "text": chapter_text,
                "hex": string_to_hex(chapter_text)
            })
    else:
        # Fallback if no logical chapters exist
        full_text = ""
        for i in range(doc.page_count):
            page = doc.load_page(i)
            full_text += get_html_from_page(page, i + 1, images)
        chapters.append({
            "title": "Document Content",
            "text": full_text,
            "hex": string_to_hex(full_text)
        })
                    
    doc.close()
    
    return {
        "chapters": chapters,
        "images": images,
        "filename": file.filename
    }
