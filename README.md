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
| Accesso a IrenYou e lettura elenco fatture | Implementato nel codice; da validare sul portale reale |
| Download Iren, baseline iniziale e riconoscimento nuove fatture | Implementato nel codice; da validare sul portale reale |
| Lettura importo, data emissione e scadenza dal portale | Implementata quando i dati sono presenti nel testo della pagina |

Il flusso Alperia è pronto per l'uso. L'automazione Iren è stata adattata alle schermate e ai percorsi forniti, ma il primo download reale deve ancora essere verificato. Non aggira CAPTCHA o sistemi MFA.

## Percorsi archivio

Il volume OMV viene montato in /archive:

- /archive/Corrente/YYYY_MM.pdf
- /archive/Gas/YYYY_MM.pdf

Esempio: Corrente/2026_11.pdf. Se esiste già un documento diverso per lo stesso mese, non viene sovrascritto: viene salvata una variante con suffisso hash. Le cartelle vengono create automaticamente.

## Acquisizione Gmail

- Host imap.gmail.com, porta 993, TLS.
- Cartella INBOX, modificabile in Environment.
- Alperia: mittente noreply@alperia.eu, oggetto che inizia con Nuova fattura disponibile (senza vincoli su Nr. o sul numero fattura).
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
5. Seleziona il progetto e fai Edit. Nel campo Environment incolla i parametri di .env.example e compila almeno IMAP_USERNAME e IMAP_APP_PASSWORD. Lascia IREN_AUTOMATION_ENABLED=false finché non hai verificato l'accesso al portale.
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

Il modello crea una notifica persistente; puoi sostituire l'azione con la notifica del telefono. Per le bollette Iren il messaggio include importo da pagare, scadenza, data di emissione e numero fattura quando disponibili. Gli stessi dati sono inviati anche come campi separati nel payload webhook (amount, due_date, issue_date, invoice_number). Per Alperia il testo della notifica rimane invariato.

## IrenYou: accesso e sincronizzazione fatture

L'email Iren è soltanto il segnale che avvia il controllo del portale. L'email non deve contenere importo o numero fattura.

Il browser segue i percorsi verificati:

1. https://clienti.irenyou.gruppoiren.it/login
2. Accesso con email e password tramite il pulsante ACCEDI.
3. https://clienti.irenyou.gruppoiren.it/area-riservata/home
4. https://clienti.irenyou.gruppoiren.it/area-riservata/spese
5. Selezione della singola fattura, apertura del dettaglio e menu Azioni → Scarica Bolletta.

### Primo avvio: inizializzazione dello storico

Al primo accesso il programma legge tutte le fatture visibili nella sezione Spese e le ordina in base alla data di scadenza, dalla più recente alla più vecchia. Scarica soltanto la prima (la più recente) e registra le altre nel database SQLite con stato historical, senza scaricarle.

La fattura più recente viene registrata come pending fino al completamento del download; dopo aver archiviato il PDF diventa downloaded. Se il download fallisce, rimane pending e potrà essere ritentato.

### Controlli successivi

A ogni nuova email Iren:

- legge nuovamente l'elenco delle fatture visibili;
- confronta l'identificativo della scheda fattura con quelli già registrati in SQLite;
- ignora le fatture in stato historical o downloaded;
- scarica soltanto quelle nuove o rimaste pending dopo un errore;
- salva nel database importo, scadenza, data di emissione e numero fattura quando rilevati dal portale.

La chiave di confronto è l'identificativo mostrato nella scheda dell'elenco; il numero fattura formale riportato nel dettaglio viene salvato separatamente. L'ordinamento iniziale usa la scadenza, perché è il dato cronologico visibile nell'elenco. Non si basa sull'ordine delle email.

Configurazione Environment:

    IREN_AUTOMATION_ENABLED=true
    IREN_USERNAME=indirizzo_email_iren
    IREN_PASSWORD=password_iren
    IREN_LOGIN_URL=https://clienti.irenyou.gruppoiren.it/login
    IREN_BILLS_URL=https://clienti.irenyou.gruppoiren.it/area-riservata/spese

Lascia vuoti i selettori personalizzati finché il riconoscimento automatico non viene verificato. In presenza di OTP/CAPTCHA/MFA il sistema si ferma e notifica, senza tentare di aggirare le protezioni.

## Dati e backup

- data/bollette.sqlite3: registro email, documenti, stato della baseline Iren e storico delle fatture Iren.
- data/tmp/iren-profile: eventuali cookie/sessioni IrenYou.
- Cartelle Corrente e Gas: bollette PDF.

Includere data/ e la cartella Bollette nei backup. Non pubblicare mai credenziali, database o profilo browser.

## Test su Gmail (senza cancellare o modificare email)

Il servizio continua a cercare sia email lette sia non lette nella cartella IMAP configurata, nella finestra degli ultimi 730 giorni. Il database SQLite impedisce di archiviare di nuovo i messaggi già completati.

Per verificare il riconoscimento senza produrre effetti:

1. In OMV → Services → Compose → Files → archivia-bollette → Edit, nel campo Environment imposta:

       TEST_MODE=scan
       TEST_PROVIDER=alperia
       TEST_UID=
       TEST_FORCE_REPROCESS=false

2. Salva e ricrea il container con Up.
3. Apri Logs. La modalità scan non archivia file, non invia notifiche e non avvia Playwright.

Per provare una singola email:

       TEST_MODE=process
       TEST_PROVIDER=alperia
       TEST_UID=UID_LETTO_NEI_LOG
       TEST_FORCE_REPROCESS=false

Per Iren usare TEST_PROVIDER=iren e l'UID della notifica Iren. Il test process esegue l'accesso al portale se IREN_AUTOMATION_ENABLED=true e può archiviare realmente i PDF.

Per testare soltanto la notifica Home Assistant senza scaricare o archiviare documenti, impostare:

       TEST_MODE=notify
       TEST_PROVIDER=iren
       TEST_UID=
       TEST_FORCE_REPROCESS=false

La modalità invia una sola notifica di esempio per avvio del container, con importo 28,16 €, scadenza 24/11/2026, data emissione e numero fattura. Non si collega a Gmail, non avvia Playwright, non modifica SQLite e non crea PDF. Dopo il test ripristinare TEST_MODE=off (altrimenti un successivo riavvio ripeterà la prova). Se HA_WEBHOOK_URL non è configurato o il webhook risponde con errore, il test viene considerato non riuscito e il log segnala il problema.

Per richiedere un singolo tentativo forzato anche su un messaggio già completato o in attesa del retry, impostare TEST_FORCE_REPROCESS=true. La forzatura viene concessa una sola volta per UID durante l'avvio corrente del container; per un ulteriore tentativo, riavviare il container mantenendo la modalità process. La deduplicazione SHA-256 resta attiva.

Terminata la prova, ripristina sempre:

       TEST_MODE=off
       TEST_PROVIDER=all
       TEST_UID=
       TEST_FORCE_REPROCESS=false

Non eliminare data/bollette.sqlite3: contiene lo storico e lo stato di elaborazione.

## Test automatici del codice

    pip install -r requirements.txt pytest
    pytest -q
