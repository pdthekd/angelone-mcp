import re

def normalize_company_name(name):
    """Normalize company name while preserving distinctive words."""
    if not name:
        return ""
        
    # Convert to lowercase
    normalized = name.lower()
    
    # Remove ONLY generic legal entity terms
    entity_terms = ["public", "plc", "limited", "ltd"]
    
    for term in entity_terms:
        normalized = normalized.replace(term + ".", "")
        normalized = normalized.replace(term + ",", "")
        normalized = normalized.replace(term, "")
    
    # Replace punctuation with spaces
    normalized = re.sub(r'[^\w\s]', ' ', normalized)
    
    # Remove multiple spaces and trim
    normalized = re.sub(r'\s+', ' ', normalized).strip()
    if normalized.split(' ')[-1] in ['india', 'company', 'industries']:
        normalized = ' '.join(normalized.split(' ')[:-1])
    
    return normalized