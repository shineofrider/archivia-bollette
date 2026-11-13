# Archivia Bollette

Servizio self-hosted per acquisire le bollette di **Alperia (corrente)** e **Iren (gas)** dalla stessa casella Gmail, archiviarle su OpenMediaVault e inviare notifiche a Home Assistant.

## Stato delle funzionalità

| Funzione | Stato |
|---|---|
| Controllo Gmail via IMAP/TLS, senza Gmail API | Implementato |
| Riconoscimento e download allegati PDF Alperia | Implementato |
| Archivio, deduplicazione SHA-256 e SQLite | Implementato |
| Notifiche via webhook Home Assistant | Implementato |
| Rilevamento email di emissione Iren | Implementato |
| Avvio Playwright solo dopo email Iren | Implementato, se abilitato |
| Login e download IrenYou | Da verificare con il DOM reale dell'account; selettori configurabili |

Il flusso Alperia è pronto per il test con la casella reale. L'automazione Iren è predisposta ma rimane disabilitata finché non sono verificati login, eventuale MFA e selettore univoco del PDF. Non aggira CAPTCHA o sistemi MFA.

## Percorsi archivio

Il volume OMV viene montato in /archive:

- /archive/Corrente/YYYY_MM.pdf
- /archive/Gas/YYYY_MM.pdf

Esempio: Corrente/2026_11.pdf. Se esiste già un documento diverso per lo stesso mese, non viene sovrascritto: viene salvata una variante con suffisso hash. Le cartelle vengono create automaticamente.

## Acquisizione Gmail

- Host imap.gmail.com, porta 993, TLS.
- Cartella INBOX, modificabile in Environment.
- Alperia: mittente noreply@alperia.eu, oggetto che inizia con Nuova fattura disponibile Nr.
- Iren: mittente noreply@mail.clienti.irenyou.gruppoiren.it, oggetto esatto Emissione Bolletta.
- Controllo predefinito ogni 5 minuti.
- Le email vengono lette in modalità read-only, senza marcarle come lette o eliminarle.

Per IMAP usare una password per app Google se disponibile con verifica in due passaggi, oppure in futuro autenticazione OAuth2. Non inserire la password principale Google.

## Installazione su OpenMediaVault (GUI Compose, senza Portainer)

Il progetto viene pubblicato come immagine GHCR. Non occorre clonare il repository sul NAS né installare Git: OMV scarica il Compose e Docker scarica l'immagine già costruita da GitHub Actions.

1. Verifica che openmediavault-compose sia installato e configurato in Services → Compose → Settings.
2. Apri Services → Compose → Files → + → Add from URL.
3. Inserisci l'URL raw:
   https://raw.githubusercontent.com/shineofrider/archivia-bollette/main/compose.yaml
4. Nome progetto: archivia-bollette.
5. Seleziona il progetto e fai Edit. Nel campo Environment incolla i parametri di .env.example e compila almeno IMAP_USERNAME e IMAP_APP_PASSWORD. Lascia IREN_AUTOMATION_ENABLED=false.
6. In Files seleziona il progetto e premi Check, poi Up.
7. Controlla Tools → Logs per verificare l'avvio.

La cartella ./data è relativa alla directory Compose gestita dal plugin e conserva SQLite e il profilo browser Iren. Il volume PDF è già impostato sul percorso OMV fornito.

### Pubblicazione iniziale e GHCR

La GitHub Action pubblica ghcr.io/shineofrider/archivia-bollette. Al primo rilascio, apri il package nella scheda Packages su GitHub e verifica la visibilità. Se privato, vai in Package settings → Change visibility → Public, così OMV può scaricare l'immagine senza credenziali. La visibilità del package è separata da quella del repository:
https://docs.github.com/en/packages/learn-github-packages/configuring-a-packages-access-control-and-visibility

### Aggiornamenti automatici del container

1. In Services → Compose → Schedule crea un job.
2. Seleziona Maintenance e abilita Update.
3. Nel filtro indica archivia-bollette per non aggiornare gli altri container.
4. Pianifica una volta al giorno o alla settimana.

OMV scaricherà l'immagine più recente e ricreerà il container se era in esecuzione. Per l'aggiornamento manuale: Files → Pull, poi Up. Documentazione:
https://wiki.omv-extras.org/doku.php?id=omv8:omv8_plugins:docker_compose

## Gmail

Nel campo Environment OMV:

    IMAP_USERNAME=indirizzo@gmail.com
    IMAP_APP_PASSWORD=password_app_google

L'app usa IMAP TLS e non richiede Gmail API. Se l'account non consente password per app, si dovrà aggiungere OAuth2 per IMAP. Non usare la password principale.

## Home Assistant

1. Crea un'automazione con trigger Webhook.
2. Genera un ID casuale e lascia attivo Only accessible from the local network.
3. Adatta il modello home-assistant/automation.yaml usando il tuo webhook_id.
4. Copia l'URL webhook in HA_WEBHOOK_URL nel campo Environment OMV.

Il modello crea una notifica persistente; puoi sostituire l'azione con la notifica del telefono.

## IrenYou

L'email Iren è il trigger: Chromium non si avvia negli altri cicli. Portale: https://clienti.irenyou.gruppoiren.it/

Per il primo avvio lascia IREN_AUTOMATION_ENABLED=false. Per completare l'automazione serviranno schermate della pagina login e dell'elenco bollette dopo accesso, oscurando i dati personali. Poi configureremo:

- IREN_USERNAME e IREN_PASSWORD
- IREN_USERNAME_SELECTOR, IREN_PASSWORD_SELECTOR, IREN_SUBMIT_SELECTOR
- eventualmente IREN_BILLS_URL e IREN_DOWNLOAD_SELECTOR

Il selettore deve individuare un solo documento recente. In presenza di OTP/CAPTCHA/MFA il sistema si ferma e notifica, senza tentare di aggirare le protezioni.

## Dati e backup

- data/bollette.sqlite3: registro email e documenti.
- data/tmp/iren-profile: eventuali cookie/sessioni IrenYou.
- Cartelle Corrente e Gas: bollette PDF.

Includere data/ e la cartella Bollette nei backup. Non pubblicare mai credenziali, database o profilo browser.

## Test

    pip install -r requirements.txt pytest
    pytest -q
