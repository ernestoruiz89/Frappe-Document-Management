import frappe
import os

MODEL_DIR = frappe.utils.get_site_path("private", "files", "models")
MODEL_PATH = os.path.join(MODEL_DIR, "tagger_model.joblib")
VECTORIZER_PATH = os.path.join(MODEL_DIR, "tagger_vectorizer.joblib")
MLB_PATH = os.path.join(MODEL_DIR, "tagger_mlb.joblib")

def train_tagger_model():
    try:
        import joblib
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.svm import LinearSVC
        from sklearn.multiclass import OneVsRestClassifier
        from sklearn.preprocessing import MultiLabelBinarizer
    except ImportError:
        frappe.log_error(title="ML Tagger Error", message="scikit-learn or joblib not installed.")
        return False

    try:
        # Fetch all ML auto-tags
        ml_tags = frappe.get_all("Document Tag", filters={"matching_algorithm": "Auto (Machine Learning)", "is_active": 1}, pluck="name")
        if not ml_tags:
            return False # No auto ML tags configured

        # Fetch all documents with OCR content
        docs = frappe.get_all("Document", filters={"ocr_status": "Completed"}, fields=["name", "ocr_content"])
        
        X_texts = []
        Y_labels = []
        
        for d in docs:
            if not d.ocr_content:
                continue
            
            # Get tags for this document
            doc_tags = frappe.get_all("Document Tag Link", filters={"parent": d.name}, pluck="tag")
            
            # Filter tags to only those that are ML-enabled (to learn them)
            # Or we can learn all tags, but we only predict ML tags. Let's learn all tags to be robust.
            if not doc_tags:
                continue
                
            X_texts.append(d.ocr_content)
            Y_labels.append(doc_tags)
            
        if not X_texts:
            return False
            
        # Ensure model directory exists
        os.makedirs(MODEL_DIR, exist_ok=True)
        
        # Train Vectorizer
        vectorizer = TfidfVectorizer(max_features=5000, stop_words='english')
        X = vectorizer.fit_transform(X_texts)
        
        # Train MultiLabelBinarizer
        mlb = MultiLabelBinarizer()
        Y = mlb.fit_transform(Y_labels)
        
        # Train Classifier
        classifier = OneVsRestClassifier(LinearSVC(class_weight='balanced'))
        classifier.fit(X, Y)
        
        # Save models
        joblib.dump(classifier, MODEL_PATH)
        joblib.dump(vectorizer, VECTORIZER_PATH)
        joblib.dump(mlb, MLB_PATH)
        
        frappe.log_error(title="ML Tagger Training Success", message=f"Successfully trained model with {len(X_texts)} documents and {len(mlb.classes_)} tags.")
        return True
        
    except Exception as e:
        import traceback
        frappe.log_error(title="ML Tagger Training Failed", message=traceback.format_exc())
        return False

def predict_tags(doc, extracted_text):
    if not extracted_text:
        return False
        
    try:
        import joblib
    except ImportError:
        return False

    if not os.path.exists(MODEL_PATH) or not os.path.exists(VECTORIZER_PATH) or not os.path.exists(MLB_PATH):
        return False
        
    try:
        # Get active ML tags
        ml_tags = set(frappe.get_all("Document Tag", filters={"matching_algorithm": "Auto (Machine Learning)", "is_active": 1}, pluck="name"))
        if not ml_tags:
            return False
            
        classifier = joblib.load(MODEL_PATH)
        vectorizer = joblib.load(VECTORIZER_PATH)
        mlb = joblib.load(MLB_PATH)
        
        # Predict
        X = vectorizer.transform([extracted_text])
        predicted_indices = classifier.predict(X)
        predicted_tags = mlb.inverse_transform(predicted_indices)[0]
        
        existing_tags = {t.tag for t in doc.get("tags", [])}
        tags_added = False
        
        for tag in predicted_tags:
            if tag in ml_tags and tag not in existing_tags:
                doc.append("tags", {"tag": tag})
                tags_added = True
                
        return tags_added
        
    except Exception as e:
        import traceback
        frappe.log_error(title="ML Tagger Prediction Failed", message=f"Doc: {doc.name}\nError: {traceback.format_exc()}")
        return False
