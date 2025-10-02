# Name of the virtual environment
VENV = .venv
PYTHON = python3.12
PIP = $(VENV)/bin/pip

# Default target
default: install

# Create a new virtual environment
$(VENV)/bin/activate: requirements.txt
	@echo "📦 Creating virtual environment..."
	rm -rf $(VENV)
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip setuptools wheel

# Install dependencies from requirements.txt
install: $(VENV)/bin/activate
	@echo "📥 Installing requirements..."
	$(PIP) install -r requirements.txt

# Clean the virtual environment
clean:
	@echo "🧹 Removing virtual environment..."
	rm -rf $(VENV)

# Rebuild everything from scratch
rebuild: clean install

# Run the app with uvicorn
run:
	$(VENV)/bin/uvicorn main:app --reload
