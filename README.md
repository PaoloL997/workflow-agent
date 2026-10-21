## Store 
Storico delle conversazioni

Utilizziamo un db sqlite
id, user, messages (testo JSON), updated_at

## API

Ogni richiesta porta l'header `Authorization: Token <token Workflow>`: l'utente
è ricavato da Workflow (`GET /api/agent/me/`) e il token non viene salvato.
Header mancante o token rifiutato → `401`.

### `POST /chat`

Invia un messaggio. Senza `conversation_id` crea una nuova conversazione; con
un `conversation_id` inesistente o di un altro utente → `404`.

```json
{"message": "La rev 1 del 22155-01-QZ03 è approvata", "conversation_id": null}
```

Risposta (stessa forma per `/confirm`):

```json
{
  "conversation_id": "3f2a…",
  "reply": null,
  "pending": [
    {
      "tool_call_id": "call-1",
      "tool_name": "change_external_status",
      "args": {"job": "22155", "vendor_doc": "22155-01-QZ03", "rev_no": 1, "new_letter": "A"},
      "summary": "Documento 22155-01-QZ03, rev 1: risposta cliente C (Commentato) → A (Approvato); la rev 2 verrà eliminata se vuota"
    }
  ]
}
```

Se la run è terminata, `reply` contiene la risposta e `pending` è vuoto. Se
`pending` non è vuoto, la run è in pausa in attesa della decisione dell'utente
su ciascuna azione.

`summary` è la frase da mostrare all'utente per l'azione, con valore attuale e
nuovo letti da Workflow al momento della risposta (`summaries.py`). Per
`change_external_status` indica anche cosa succede alla revisione successiva
(`; verrà creata la rev <n+1>` oppure `; la rev <n+1> verrà eliminata se
vuota`). Per `undo_submission` indica l'ultima revisione del documento e la
data di invio che verrà cancellata (`Documento 22155-01-QZ03, rev B (inviata il
01/09/2026): annulla l'invio al cliente; torna "da emettere"`). Per i tool
senza un summary dedicato, o se la lettura da Workflow
fallisce, vale `<tool_name>(<chiave>=<valore>, ...)`, ad esempio
`change_external_status(job='22155', vendor_doc='22155-01-QZ03', rev_no=1, new_letter='A')`.

### `POST /confirm`

Riprende la run in pausa con le decisioni dell'utente: `true` esegue l'azione,
`false` la rifiuta.

```json
{"conversation_id": "3f2a…", "approvals": {"call-1": true}}
```

Le chiavi di `approvals` devono essere esattamente i `tool_call_id` delle azioni
in sospeso, né uno in più né uno in meno.

| Codice | Quando | Corpo |
|---|---|---|
| `404` | conversazione inesistente o di un altro utente | `{"detail": "Conversation not found"}` |
| `409` | la conversazione non ha azioni in sospeso | `{"detail": "No pending actions for this conversation"}` |
| `400` | id in `approvals` mancanti o sconosciuti | `{"detail": "Pending actions do not match", "expected": [...], "received": [...]}` |
