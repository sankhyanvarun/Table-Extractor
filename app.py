import os
import re
import csv
import uuid
import tempfile
import PyPDF2
from flask import Flask, render_template, request, jsonify, send_file
from pdf2image import convert_from_path
import pytesseract
import subprocess
import shutil
import io
import time

app = Flask(__name__)

# Configure paths (update these for your system)
POPPLER_PATH = r'bin'  # Example for Windows
# TESSERACT_CMD = r'C:\Program Files\Tesseract-OCR\tesseract.exe'  # Example for Windows
TESSERACT_CMD = r'tesseract-ocr-w64-setup-5.5.0.20241111.exe'  # Windows example

# Set Tesseract command
pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

def get_poppler_path():
    """Get poppler path from environment or use configured path."""
    # Check if poppler exists in the configured path
    if POPPLER_PATH and os.path.exists(os.path.join(POPPLER_PATH, 'pdftoppm')):
        return POPPLER_PATH
    # Check common installation paths
    common_paths = [
        r'C:\Program Files\poppler-24.02.0\Library\bin',
        r'C:\Program Files\poppler-23.11.0\Library\bin',
        r'C:\Program Files\poppler\bin',
        '/usr/local/bin',
        '/usr/bin',
        '/opt/homebrew/bin'
    ]
    for path in common_paths:
        if os.path.exists(os.path.join(path, 'pdftoppm')):
            return path
    return None

def extract_text_from_pdf(pdf_path):
    """Extract text from PDF using PyPDF2 and fallback to OCR for non-text pages."""
    text = ""
    poppler_path = get_poppler_path()
    
    with open(pdf_path, 'rb') as file:
        reader = PyPDF2.PdfReader(file)
        num_pages = len(reader.pages)
        for page_num in range(num_pages):
            page = reader.pages[page_num]
            page_text = page.extract_text()
            if not page_text.strip():
                # Use OCR for the page
                try:
                    images = convert_from_path(
                        pdf_path, 
                        first_page=page_num+1, 
                        last_page=page_num+1,
                        poppler_path=poppler_path
                    )
                    if images:
                        page_text = pytesseract.image_to_string(images[0])
                except Exception as e:
                    print(f"OCR failed on page {page_num+1}: {str(e)}")
                    page_text = ""  # Use empty string if OCR fails
            text += page_text + "\n"
    return text

def parse_toc(text):
    """Parse the table of contents from extracted text with enhanced patterns."""
    entries = []
    # Enhanced patterns to match TOC lines
    patterns = [
        # Pattern for: Chapter 1: Title...............1
        r'^(.*?(?:chapter|part|section|appendix|preface|foreword|introduction|references|index|acknowledgements?|bibliography)\b[\s\S]*?)[\s\.\-]*(\d+)\s*$',
        # Pattern for: 1. Title.......................1
        r'^\s*(\d+\..*?)[\s\.\-]+(\d+)\s*$',
        # Pattern for: Title.......................1
        r'^(.*?)[\s\.\-]+(\d+)\s*$',
        # Pattern for: Title - 1
        r'^(.*?)[\s\-\_]+(\d+)\s*$'
    ]
    
    # Skip lines containing these keywords (case-insensitive)
    skip_keywords = [
        "table of contents", "contents", "toc", "page", "pages", 
        "chapter", "section", "part", "continued"
    ]
    
    for line in text.split('\n'):
        line = line.strip()
        if not line:
            continue
        
        # Skip lines that are too short or contain skip keywords
        if len(line) < 5 or any(keyword in line.lower() for keyword in skip_keywords):
            continue
        
        for pattern in patterns:
            match = re.match(pattern, line, re.IGNORECASE)
            if match:
                chapter = match.group(1).strip()
                page = match.group(2).strip()
                # Validate page number is a positive integer
                if page.isdigit() and int(page) > 0:
                    entries.append({
                        "chapter": chapter,
                        "page": page
                    })
                    break  # Break after first match
    
    return entries

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    # Create a temporary directory to store the file
    temp_dir = tempfile.mkdtemp()
    pdf_path = os.path.join(temp_dir, file.filename)
    file.save(pdf_path)
    
    try:
        # Extract text from PDF
        text = extract_text_from_pdf(pdf_path)
        # Parse TOC from text
        toc = parse_toc(text)
        return jsonify({
            'status': 'success',
            'toc': toc,
            'filename': file.filename
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        # Clean up: remove the temporary directory and its contents
        shutil.rmtree(temp_dir, ignore_errors=True)

@app.route('/preview', methods=['POST'])
def preview_pdf():
    """Generate a preview of the first page of the PDF."""
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
    
    file = request.files['file']
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    # Save to temp file
    temp_dir = tempfile.mkdtemp()
    pdf_path = os.path.join(temp_dir, file.filename)
    file.save(pdf_path)
    
    try:
        poppler_path = get_poppler_path()
        images = convert_from_path(
            pdf_path, 
            first_page=1, 
            last_page=1,
            poppler_path=poppler_path
        )
        
        if images:
            # Convert image to bytes
            img_byte_arr = io.BytesIO()
            images[0].save(img_byte_arr, format='JPEG')
            img_byte_arr = img_byte_arr.getvalue()
            return img_byte_arr, 200, {'Content-Type': 'image/jpeg'}
        
        return jsonify({'error': 'Could not generate preview'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)

@app.route('/download', methods=['POST'])
def download_csv():
    data = request.get_json()
    if not data or not data.get('toc'):
        return jsonify({'error': 'No TOC data provided'}), 400
    
    # Create a temporary CSV file
    temp_dir = tempfile.mkdtemp()
    csv_path = os.path.join(temp_dir, 'toc.csv')
    
    try:
        with open(csv_path, 'w', newline='', encoding='utf-8') as csvfile:
            writer = csv.writer(csvfile)
            writer.writerow(['Chapter Name', 'Page Number'])
            for item in data['toc']:
                writer.writerow([item['chapter'], item['page']])
        
        return send_file(csv_path, as_attachment=True, download_name='table_of_contents.csv')
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        # Clean up the temporary directory after sending the file
        shutil.rmtree(temp_dir, ignore_errors=True)

if __name__ == '__main__':
    # Check Poppler installation
    poppler_path = get_poppler_path()
    if poppler_path:
        print(f"Poppler found at: {poppler_path}")
    else:
        print("Warning: Poppler not found. OCR may not work.")
    
    app.run(debug=True, port=5000)