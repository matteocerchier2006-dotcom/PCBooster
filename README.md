# PC Booster

App per Windows 10/11 che tiene in forma il PC: driver della scheda video, aggiornamenti di processore/chipset e Windows, ottimizzazioni per i giochi, programmi all'avvio, pulizia dei file inutili e disinstallazione di app e giochi.

## Cosa fa

- **Modalità partita**: prima di giocare attiva Prestazioni elevate, libera la RAM e chiude le app scelte; alla fine rimette tutto com'era.
- **Panoramica**: voto da 0 a 100 sullo stato del PC, specifiche, uso di CPU, RAM e disco, e la lista di cosa sistemare.
- **Pulizia**: temporanei, Windows Update, report e dump di errore, log, cache di Chrome/Edge/Brave/Firefox/Zen/Opera/Vivaldi, Discord, Spotify, Steam, Epic, cache shader e pulizia profonda (componenti di Windows, Windows.old).
- **Dischi**: spazio libero, Libera spazio, salute di SSD e hard disk (stato, vita residua, temperatura, ore di accensione), ottimizzazione delle unità (TRIM / deframmentazione) e ricerca dei file grandi.
- **Sicurezza**: Microsoft Defender (controllo veloce, completo e offline, definizioni, minacce e rimozione) e firewall.
- **Scheda video**: versione driver come la mostra il produttore, memoria video reale, schermo, temperatura e utilizzo in tempo reale (NVIDIA), ottimizzazioni per i giochi (anche giochi in finestra e mouse senza accelerazione).
- **Aggiornamenti**: processore, scheda madre, BIOS, Windows Update e aggiornamento di tutti i programmi con winget.
- **Driver NVIDIA e AMD**: controlla online l'ultima versione, la scarica e apre l'installazione ufficiale.
- **Avvio automatico** e **App e giochi**.
- **Installa app**: catalogo di programmi sicuri (browser, launcher di giochi, utilità…) scaricati dalle fonti ufficiali con winget; ne scegli quanti vuoi e li installa da solo.
- **Rete**: test di velocità, ping verso i server di gioco, prova dei DNS e cambio con un clic.
- **Cronologia**: tutto quello che l'app cambia, con il tasto Annulla per ottimizzazioni, avvio automatico e DNS.
- **Segnala un problema**: crea un report sul desktop (nome utente nascosto) e apre la pagina per segnalarlo su GitHub.
- **Italiano e inglese**: la lingua segue quella di Windows, oppure la scegli in Impostazioni → Aspetto.
- **Icona vicino all'orologio**: resta attivo chiudendo la finestra, parte con Windows, manda notifiche (driver nuovo, aggiornamenti, spazio da liberare) e dal menu attiva Modalità partita e Pulizia veloce.
- **Impostazioni**: temi, colori, caratteri, angoli, barra compatta; aggiornamento automatico; pulizia programmata; punto di ripristino.

## Installazione

Al primo avvio PC Booster si copia in `C:\Program Files\PC Booster` (cartella protetta) e crea i collegamenti sul desktop e nel menu Start. Si apre senza chiedere i permessi tramite l'attività di Windows "PC Booster - Avvio".

## Come funziona

GitHub crea da solo `PCBooster.exe` ogni volta che pubblichi una nuova release. Sui PC non serve installare niente: si scarica l'exe una volta e poi l'app si aggiorna da sola.

## Pubblicare una nuova versione

1. Carica i file modificati con **Add file → Upload files**.
2. Vai su **Releases → Draft a new release**.
3. Crea un tag con un numero più alto del precedente (es. `v1.0.1`) e premi **Publish release**.
4. Dopo 3-5 minuti l'exe è pronto (scheda **Actions**) e i PC si aggiornano da soli al prossimo avvio.

## File

- `app.py`: apre la finestra
- `backend.py`: tutte le operazioni sul PC (PowerShell, registro, Windows Update)
- `cleaner.py`: pulizia e file grandi
- `drivers.py`: ultimo driver NVIDIA e AMD
- `apptools.py`: aggiornamento programmi (winget) e Modalità partita
- `build.py` e `icon.ico`: creazione dell'exe con icona e versione (li usa GitHub)
- `updater.py`: aggiornamento automatico dell'app e impostazioni
- `launcher.py`: avvio senza la richiesta dei permessi
- `tray.py`: icona vicino all'orologio, notifiche, una sola istanza
- `installer.py`: installazione in Program Files e riparazione dei collegamenti
- `security.py`: Microsoft Defender, firewall e salute dei dischi
- `network.py`: test di velocità, ping e DNS
- `history.py`: cronologia e Annulla
- `diagnostics.py`: registro degli errori e report dei problemi
- `i18n.py`, `ui/i18n.js`, `ui/lang-en.js`: lingue (il dizionario inglese è in `ui/lang-en.js`)
- `winutil.py`: comandi di Windows (PowerShell, schtasks…) con tempo massimo e salvataggi sicuri
- `ui/`: interfaccia (HTML, CSS, JavaScript)
- `.github/workflows/pubblica.yml`: istruzioni per GitHub su come creare l'exe
