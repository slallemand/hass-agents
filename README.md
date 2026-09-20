# hass-agents

Couche multi-agents pour **analyser la consommation** Home Assistant : baselines statistiques, détection d’écarts, rapports quotidien / hebdo / mensuel.

Home Assistant reste la source de vérité. Les agents **ne commandent aucun appareil** — ils publient des rapports (MQTT / JSON).

## Architecture

```
HA (states + history + statistics)
        │
        ▼
 Context Builder (chiffres déterministes)
        │
        ▼
 CrewAI (optionnel, Infomaniak Gemma 4 31B)
   Data → Anomaly → Report
        │
        ▼
 Rapport JSON + MQTT
```

Sans clé LLM : rapport **heuristique** (mêmes chiffres, narratif template).

## Prérequis

- Home Assistant + token long-lived (lecture)
- Broker MQTT **déjà utilisé par HA** (Zigbee2MQTT / add-on Mosquitto / etc.) — renseigner `MQTT_*` dans `.env`
- Compte [Infomaniak AI Tools](https://www.infomaniak.com/fr/hebergement/ai-services/tarifs) (optionnel)

## Installation

```bash
cp .env.example .env
# renseigner HA_URL, HA_TOKEN, MQTT_HOST (broker HA), et éventuellement Infomaniak
```

Exemple MQTT (broker HA existant) :

```env
MQTT_HOST=192.168.x.x          # ou hostname du broker / add-on
MQTT_PORT=1883
MQTT_USERNAME=...
MQTT_PASSWORD=...
MQTT_TOPIC_PREFIX=house
```

```bash
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
# Avec LLM CrewAI (Python 3.11–3.13, ex. image Docker) :
pip install -e ".[llm]"
```

Sur Python 3.14 local, CrewAI n’est pas encore dispo : le mode **heuristique** fonctionne quand même. L’image Docker (`hi/python:3.12`) installe `[llm]`.

Infomaniak :

```env
OPENAI_API_KEY=<token_api_infomaniak>
OPENAI_API_BASE=https://api.infomaniak.com/2/ai/<product_id>/openai/v1
LLM_MODEL=google/gemma-4-31B-it
```

Mapping entités : [`config/entities.yaml`](config/entities.yaml) (déjà prérempli depuis ton Energy Dashboard).

## Usage

```bash
# Rapport heuristique / LLM selon .env
hass-agents analyze-once --period daily
hass-agents analyze-once --period weekly --mqtt
hass-agents analyze-once --period monthly -o reports/monthly.json

# Écoute MQTT house/agents/consumption/request (même broker que HA)
hass-agents serve
```

Docker :

```bash
# Image Red Hat Hardened (catalog https://images.redhat.com)
# Pull: registry.access.redhat.com/hi/python:3.12
# Le broker MQTT n’est PAS embarqué — utiliser celui de HA via .env
docker compose up --build -d
```

Base utilisée : `registry.access.redhat.com/hi/python:3.12` (+ `-builder` multi-stage).

## Package Home Assistant

Copier [`homeassistant/packages/consumption_ai.yaml`](homeassistant/packages/consumption_ai.yaml) dans `config/packages/` (packages activés).  
Automations : demande daily 21h, weekly dimanche, monthly le 1er ; notification si `warn`/`critical`.

## Tests

```bash
pytest -q
```

## Sécurité

- Client HA : `GET` states/history + websocket statistics **uniquement**
- Aucun `call_service` d’écriture dans le code agents
- MQTT : topics `house/agents/consumption/*` (rapports / requêtes)
