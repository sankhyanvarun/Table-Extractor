import os
import re
import csv
import tempfile
import PyPDF2
from flask import Flask, render_template, request, jsonify, send_file
from pdf2image import convert_from_path
import pytesseract
import shutil

app = Flask(__name__)

# Configure paths
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
POPPLER_PATH = os.path.join(BASE_DIR, 'poppler', 'bin')
TESSERACT_CMD = r'C:\Program Files\Tesseract-OCR\tesseract.exe'

# Set Tesseract command
pytesseract.pytesseract.tesseract_cmd = TESSERACT_CMD

def get_poppler_path():
    """Get poppler path from the project directory"""
    return POPPLER_PATH

def convert_hindi_digits(text):
    """Convert Hindi (Devanagari) digits to Arabic numerals"""
    hindi_digits = {
        '०': '0', '१': '1', '२': '2', '३': '3', '४': '4',
        '५': '5', '६': '6', '७': '7', '८': '8', '९': '9'
    }
    return ''.join(hindi_digits.get(char, char) for char in text)

def extract_text_from_pdf(pdf_path, lang='eng'):
    """Extract text from PDF using PyPDF2 with OCR fallback"""
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
                        poppler_path=poppler_path,
                        dpi=400,  # Higher DPI for better Hindi OCR
                        grayscale=True  # Better for text recognition
                    )
                    if images:
                        # Use specified language for OCR
                        config = '--psm 6'  # Assume a single uniform block of text
                        page_text = pytesseract.image_to_string(images[0], lang=lang, config=config)
                except Exception as e:
                    print(f"OCR failed on page {page_num+1}: {str(e)}")
                    page_text = ""  # Use empty string if OCR fails
            text += page_text + "\n"
    return text

def is_valid_page_number(page_str):
    """Check if string contains only digits (Arabic or Hindi)"""
    if not page_str:
        return False
    # Check for Arabic digits (0-9) or Hindi digits (०-९)
    return all(char in '0123456789०१२३४५६७८९' for char in page_str)

def parse_toc(text, is_hindi=False):
    """Parse the table of contents from extracted text"""
    entries = []
    
    # Common patterns for both languages
    common_patterns = [
        r'^(.*?)[\s\.\-]+(\d+)\s*$',          # Title........123
        r'^(.*?)[\s\-\_]+(\d+)\s*$',           # Title - 123
        r'^\s*(\d+\..*?)[\s\.\-]+(\d+)\s*$',  # 1. Title...123
    ]
    
    # Hindi-specific patterns - improved for Hindi TOC structures
    hindi_patterns = [
        # Hindi numbering: १. शीर्षक १२३
        r'^\s*([०१२३४५६७८९]+\.\s+.*?)[\s\.\-]*([०१२३४५६७८९\d]+)\s*$',
        # Hindi with separator: शीर्षक - १२३
        r'^(.*?)[\s\-\—]+([०१२३४५६७८९\d]+)\s*$',
        # Hindi with dots: शीर्षक........१२३
        r'^(.*?)[\s\.]+([०१२३४५६७८९\d]+)\s*$',
        # Chapter headings: अध्याय १: शीर्षक १२३
        r'^(.*?(?:अध्याय|खंड|परिशिष्ट|प्रस्तावना|भाग|अनुभाग|प्रकरण)\s*[०१२३४५६७८९]*[\.\:\-]?\s*.*?)[\s\.\-]*([०१२३४५६७८९\d]+)\s*$',
        # Generic Hindi: any text followed by Hindi/Arabic digits at the end
        r'^(.*?)\s+([०१२३४५६७८९\d]+)$'
    ]
    
    patterns = hindi_patterns if is_hindi else common_patterns
    
    # Skip terms
    skip_terms_eng = ["table of contents", "contents", "page", "chap"]
    skip_terms_hindi = ["विषय सूची", "अनुक्रमणिका", "सामग्री", "पृष्ठ", "अध्याय"]
    skip_terms = skip_terms_hindi if is_hindi else skip_terms_eng
    
    for line in text.split('\n'):
        line = line.strip()
        if not line or len(line) < 5:
            continue
        
        # Skip common TOC headers
        if any(term in line.lower() for term in skip_terms):
            continue
            
        for pattern in patterns:
            match = re.match(pattern, line, re.IGNORECASE | re.UNICODE)
            if match:
                # Handle different group patterns
                if len(match.groups()) == 2:
                    chapter = match.group(1).strip()
                    page = match.group(2).strip()
                elif len(match.groups()) == 3:
                    # For patterns with 3 groups (like numbered headings)
                    chapter = f"{match.group(1)} {match.group(2)}".strip()
                    page = match.group(3).strip()
                else:
                    continue
                
                # Validate page number (Arabic or Hindi digits)
                if is_valid_page_number(page):
                    # Convert Hindi digits to Arabic numerals
                    page = convert_hindi_digits(page)
                    entries.append({
                        "chapter": chapter,
                        "page": page
                    })
                    break
                else:
                    # Fallback: Look for digits at the end of the line
                    digit_match = re.search(r'(\d+|[०१२३४५६७८९]+)$', line)
                    if digit_match:
                        page = digit_match.group(1)
                        if is_valid_page_number(page):
                            chapter = line[:digit_match.start()].strip()
                            page = convert_hindi_digits(page)
                            entries.append({
                                "chapter": chapter,
                                "page": page
                            })
                            break
    
    return entries

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/upload', methods=['POST'])
def upload_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file uploaded'}), 400
        
    file = request.files['file']
    language = request.form.get('language', 'eng')  # Default to English
    
    if file.filename == '':
        return jsonify({'error': 'No selected file'}), 400
    
    # Create a temporary directory
    temp_dir = tempfile.mkdtemp()
    pdf_path = os.path.join(temp_dir, file.filename)
    file.save(pdf_path)
    
    try:
        # Determine if we need Hindi-specific parsing
        is_hindi = (language == 'hin' or language == 'both')
        # For OCR, we can pass the language code. If both, we pass 'eng+hin'
        ocr_lang = language
        if language == 'both':
            ocr_lang = 'eng+hin'
        
        # Extract text from PDF
        text = extract_text_from_pdf(pdf_path, lang=ocr_lang)
        
        # Parse TOC from text
        toc = parse_toc(text, is_hindi=is_hindi)
        return jsonify({
            'status': 'success',
            'toc': toc,
            'filename': file.filename
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        # Clean up
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
        with open(csv_path, 'w', newline='', encoding='utf-8-sig') as csvfile:  # UTF-8 with BOM for Excel
            writer = csv.writer(csvfile)
            writer.writerow(['Chapter Name', 'Page Number'])
            for item in data['toc']:
                writer.writerow([item['chapter'], item['page']])
        
        return send_file(csv_path, as_attachment=True, download_name='table_of_contents.csv')
    except Exception as e:
        return jsonify({'error': str(e)}), 500
    finally:
        # Clean up after sending the file
        shutil.rmtree(temp_dir, ignore_errors=True)

if __name__ == '__main__':
    # Verify poppler installation
    poppler_path = get_poppler_path()
    if poppler_path and os.path.exists(os.path.join(poppler_path, 'pdftoppm.exe')):
        print(f"Poppler found at: {poppler_path}")
    else:
        print("Warning: Poppler not found. OCR may not work properly")
    
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)))




