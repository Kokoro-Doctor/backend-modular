# SAM invokes: make build-OCRCoreLayer with ARTIFACTS_DIR set.
# Lambda layers must expose packages under python/ so that /opt/python is on sys.path.
build-OCRCoreLayer:
	mkdir -p "$(ARTIFACTS_DIR)/python"
	cp -r python/ocr_core "$(ARTIFACTS_DIR)/python/"
