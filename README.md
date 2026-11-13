# Archivia Bollette

Servizio self-hosted per acquisire e archiviare le bollette di Alperia (corrente) e Iren (gas), con notifiche Home Assistant.

## Destinazione archivio

Il Compose monta la cartella OMV:

`/srv/dev-disk-by-uuid-4f71b583-cef3-4261-ac1a-dad3a9057bb9/Documenti/Casa/Bollette`

Struttura attesa:

- `Corrente/YYYY_MM.pdf`
- `Gas/YYYY_MM.pdf`

Le directory `Corrente` e `Gas` devono esistere già. Il servizio non rinomina né sposta altri documenti presenti.

## Comportamento previsto

- Controllo IMAP della stessa casella Gmail ogni 5 minuti.
- Alperia: riconosce mittente `noreply@alperia.eu`, oggetto `Nuova fattura disponibile Nr. ...` e scarica il PDF allegato.
- Iren: riconosce mittente `noreply@mail.clienti.irenyou.gruppoiren.it`, oggetto `Emissione Bolletta`; solo allora avvia l'automazione browser per il portale IrenYou.
- Notifiche tramite webhook locale Home Assistant.
- Database e stato di elaborazione persistenti in `./data`.
- Nessuna porta pubblicata e nessun servizio/API esterna a pagamento.

**Nota sullo stato:** il repository contiene la base di deployment e la configurazione. Il flusso IMAP, il parser dei PDF e soprattutto i selettori Playwright di IrenYou devono essere implementati e verificati con messaggi e portale reali prima dell'uso operativo. Non impostare `IREN_AUTOMATION_ENABLED=true` finché login, eventuale MFA e download non sono stati testati.

## Deploy su OpenMediaVault GUI (senza Portainer)

1. In OMV crea una cartella di lavoro, ad esempio `/srv/dev-disk-by-uuid-.../appdata/archivia-bollette`.
2. Scarica/clona questo repository in quella cartella (in alternativa usa Git dalla shell una sola volta).
3. Verifica che nella cartella archivio esistano `Corrente` e `Gas`.
4. Copia `.env.example` in `.env` e inserisci le credenziali solo localmente sul server. Non caricare mai `.env` su GitHub.
5. In OMV apri **Services → Compose → Files** (la denominazione può variare con la versione del plugin).
6. Aggiungi un file Compose indicando la cartella del progetto e seleziona `compose.yaml`.
7. Usa **Up** per creare/avviare, **Logs** per visualizzare i log e **Down** per arrestare/rimuovere il container mantenendo i dati nei bind mount.
8. Per aggiornare il codice: aggiorna il repository sul server, poi esegui **Pull** (se immagine remota disponibile) oppure **Build/Up** secondo il metodo di distribuzione scelto.

### Importante: immagine container

Il riferimento GHCR nel Compose è il target previsto per il rilascio dell'immagine. Finché non viene pubblicata una GHCR image con GitHub Actions, il container non potrà essere avviato da quel riferimento. Per la prima installazione si può usare una variante di Compose con `build: .` e Dockerfile incluso, così OMV costruisce l'immagine localmente.

## Credenziali Gmail

Usare IMAP su TLS (imap.gmail.com:993). Preferire OAuth2; in alternativa, se l'account Google lo consente, utilizzare una password per app con verifica in due passaggi. La password deve rimanere in `.env` sul server.

## Home Assistant

Creare una automazione con trigger webhook e inserire in `HA_WEBHOOK_URL` un URL raggiungibile solo dalla rete Docker/LAN. Non esporre il webhook su Internet. Trattare l'ID webhook come un segreto.

## Sicurezza e affidabilità

- Backup della cartella `data` e dell'archivio PDF.
- Permessi restrittivi su `.env` (ad esempio chmod 600).
- Verificare che i file PDF scaricati siano validi e non vuoti.
- Non sovrascrivere un PDF esistente senza confronto/hash e conferma.
- In caso di login Iren/MFA/CAPTCHA non riuscito, notificare e richiedere intervento manuale; non tentare di aggirare le protezioni.
