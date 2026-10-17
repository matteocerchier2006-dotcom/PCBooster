# PC Booster

App per Windows 10/11 che tiene in forma il PC: driver della scheda video, aggiornamenti di processore/chipset e Windows, ottimizzazioni per i giochi, programmi all'avvio, pulizia dei file inutili e disinstallazione di app e giochi.

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
- `updater.py`: aggiornamento automatico dell'app
- `ui/`: interfaccia (HTML, CSS, JavaScript)
- `.github/workflows/pubblica.yml`: istruzioni per GitHub su come creare l'exe
