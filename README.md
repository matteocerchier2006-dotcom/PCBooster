# PC Booster

App per Windows 10/11 che tiene in forma il PC: driver della scheda video, aggiornamenti di processore/chipset e Windows, ottimizzazioni per i giochi, programmi all'avvio, pulizia dei file inutili e disinstallazione di app e giochi.

## Cosa fa

- **Modalità partita**: prima di giocare attiva Prestazioni elevate, libera la RAM e chiude le app scelte; alla fine rimette tutto com'era.
- **Panoramica**: voto da 0 a 100 sullo stato del PC, specifiche, uso di CPU, RAM e disco, e la lista di cosa sistemare.
- **Pulizia**: temporanei, Windows Update, report e dump di errore, log, cache di Chrome/Edge/Brave/Firefox/Zen/Opera/Vivaldi, Discord, Spotify, Steam, Epic, cache shader e pulizia profonda (componenti di Windows, Windows.old).
- **Spazio su disco**: dischi (SSD/HDD) e ricerca dei file grandi, con spostamento nel Cestino.
- **Scheda video**: versione driver come la mostra il produttore, memoria video reale, schermo, temperatura e utilizzo in tempo reale (NVIDIA), ottimizzazioni per i giochi.
- **Aggiornamenti**: processore, scheda madre, BIOS, Windows Update e aggiornamento di tutti i programmi con winget.
- **Driver NVIDIA e AMD**: controlla online l'ultima versione, la scarica e apre l'installazione ufficiale.
- **Avvio automatico** e **App e giochi**.
- **Impostazioni**: temi, colori, caratteri, angoli, barra compatta; aggiornamento automatico; pulizia programmata; punto di ripristino.

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
- `ui/`: interfaccia (HTML, CSS, JavaScript)
- `.github/workflows/pubblica.yml`: istruzioni per GitHub su come creare l'exe
