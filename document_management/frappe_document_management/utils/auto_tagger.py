import frappe
import re

def apply_auto_tags(doc, extracted_text):
    if not extracted_text:
        return False

    # Normalize text for simple matching
    text_lower = extracted_text.lower()
    
    # Get all active tags with matching rules
    tags = frappe.get_all("Document Tag", 
                          filters={"is_active": 1}, 
                          fields=["name", "matching_algorithm", "match_pattern"])
    
    # Track existing tags on the document to avoid duplicates
    existing_tags = {t.tag for t in doc.get("tags", [])}
    tags_added = False
    
    for tag in tags:
        if tag.name in existing_tags:
            continue
            
        algorithm = tag.matching_algorithm
        pattern = tag.match_pattern
        
        if algorithm == "None" or not algorithm:
            continue
            
        is_match = False
        
        if algorithm == "Auto (Machine Learning)":
            # Handled separately by ml_tagger
            continue
            
        if not pattern:
            continue
            
        if algorithm == "Exact match":
            is_match = pattern.lower() in text_lower
            
        elif algorithm == "Any word":
            words = pattern.lower().split()
            is_match = any(word in text_lower for word in words)
            
        elif algorithm == "All words":
            words = pattern.lower().split()
            is_match = all(word in text_lower for word in words)
            
        elif algorithm == "Regular expression":
            try:
                if re.search(pattern, extracted_text, re.IGNORECASE):
                    is_match = True
            except Exception as e:
                frappe.log_error(title="Auto Tagger Regex Error", message=f"Tag: {tag.name}\nPattern: {pattern}\nError: {str(e)}")
                
        if is_match:
            doc.append("tags", {"tag": tag.name})
            existing_tags.add(tag.name)
            tags_added = True
            
    return tags_added
