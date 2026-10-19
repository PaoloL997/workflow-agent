# Workflow-agent

Servizio che permette agli utenti di Workflow di correggere in autonomia dati di documenti e revisioni usando il linguaggio naturale.
Ogni modifica è proposta dal modello ed eseguita solo dopo conferma esplicita dell'utente, tramite le API di Workflow.

## Architettura

- **workflow-agent**: riceve il messaggio, orchestra modello e tool, gestisce la pausa per la conferma, persiste le conversazioni ed espone un'API HTTP al widget (da implementare).
- **Workflow**: espone le operazioni di dominio come endpoint REST, applica permessi, regole e transazioni, e ospita il widget chat nella UI.

## Flusso

1. L'utente scrive un messaggio nel widget, che chiama l'agent con `POST /chat`.
2. L'agent lancia una run con `AgentDeps(user, http_client)` e, in base alla richiesta, chiama i tool di lettura o di scrittura.
3. I tool di scrittura mettono la run in pausa: l'agent restituisce le azioni proposte.
4. L'utente approva o rifiuta ciascuna azione; il widget chiama `POST /confirm`.
5. L'agent ricarica la conversazione, costruisce `DeferredToolResults` e rilancia la run: i tool approvati eseguono la chiamata API di scrittura.

## Componenti da implementare

- `agent.py` — Agent Pydantic AI
- `deps.py` — `AgentDeps(user, client)`
- `store.py` — persistenza delle conversazioni
- `api.py` — `POST /chat`, `POST /confirm`, `GET /conversations/{id}` (FastAPI)
- `config.py` — `Settings`: URL di Workflow, chiave del provider o configurazione dell'inference server locale, modello


Quale è il funzionamento che voglio garantire?

change_external_status(documento, revisione, nuova_risposta) richiede:

**Procedura**:
GET api/commesse/<job>/documenti/    -> documenti del job, con id
GET api/documenti/<id>/revisioni/    -> revisioni del documento
GET api/stati-esterni/               -> stati esterni (id, lettera, nome, crea_nuova_rev)
POST api/revisioni/<id>/risposta-cliente/  -> cambia la risposta del cliente con le regole  [**DA CREARE**]