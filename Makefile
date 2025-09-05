# Nome del venv
VENV = .venv
PYTHON = python3.12
PIP = $(VENV)/bin/pip

# Target predefinito
default: install

# Crea un nuovo venv
$(VENV)/bin/activate: requirements.txt
	@echo "📦 Creazione venv..."
	rm -rf $(VENV)
	$(PYTHON) -m venv $(VENV)
	$(PIP) install --upgrade pip setuptools wheel

# Installa le dipendenze da requirements.txt
install: $(VENV)/bin/activate
	@echo "📥 Installazione requirements..."
	$(PIP) install -r requirements.txt

# Pulisce il venv
clean:
	@echo "🧹 Rimozione venv..."
	rm -rf $(VENV)

# Rifa tutto da zero
rebuild: clean install

# Avvia l'app con uvicorn
run:
	$(VENV)/bin/uvicorn main:app --reload
