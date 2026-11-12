# DevKit

Lokalne narzędzia do pracy nad projektem. Python (tylko biblioteka standardowa) + jeden plik HTML.

## Układ w repozytorium
```
repo/
  DevKit.exe            <- skompilowany (build.bat)
  DevKit/
    devkit.py  claudechat.py  dumpparse.py  ui/index.html  build.bat  run.bat
    data/               <- tworzy się sam: config.json, dumps/*.sql, claude-chat.json, firefox-profile/
  web/  api/            <- tworzy zakładka Projekt
```

## Start
- Bez kompilacji: `run.bat` (Python 3.8+)
- Skompilowany: `build.bat` raz (zainstaluje PyInstaller), potem `DevKit.exe` z roota repo

Ikona: `icon.ico` trafia do `DevKit.exe` (ikona pliku, build.bat robi to sam), a `ui/icon.png` jest ikoną okna na pasku zadań. Żeby zmienić wygląd, podmień oba pliki.

DevKit otwiera się we własnym oknie (Edge/Chrome/Brave w trybie aplikacji). Zamknięcie okna zamyka program i zatrzymuje uruchomione projekty.

## Baza danych
- **Serwer XAMPP:** przyciski Uruchom/Zatrzymaj dla MySQL, Apache i obu naraz, statusy z kropkami, phpMyAdmin.
- **Nazwa bazy:** litery, cyfry, `_` i `-` (nie na początku). Zapisana nazwa to plik `DevKit/data/dumps/<nazwa>.sql`.
- **Importuj:** tworzy bazę i wczytuje zapisany plik.
- **Eksportuj:** zapisuje zrzut, sprawdza że jest kompletny i dopiero wtedy usuwa bazę. Poprzedni zrzut zostaje jako `<nazwa>.prev.sql`.
- Kolory przycisków podpowiadają następny krok: pełny = zalecany, obrys = możliwy, szary = niedostępny.

## Projekt (web / api)
Przyciski play, restart i stop uruchamiają polecenie z ustawień (domyślnie `npm run dev` i `npm start`), a konsola rozsuwa się, gdy projekt działa. Przycisk z ikoną terminala włącza i wyłącza tę samą konsolę także wtedy, gdy proces nie działa (historia po awarii). Po awarii procesu konsola sama się nie zwija, zwija się dopiero po Zatrzymaj.

- **Zależności:** jeśli w folderze projektu nie ma `node_modules`, przed startem uruchamia się polecenie instalacji (domyślnie `npm install`). Gdy się nie uda, projekt się nie uruchamia.
- **Procesy z poprzedniej sesji:** DevKit sprawdza port projektu (web 3000, api 4000; `PORT` ze zmiennych środowiskowych ma pierwszeństwo). Zajęty port oznacza „Działa (uruchomiony poza DevKit)". Play, restart i stop kończą wszystko, co trzyma ten port (razem z procesami potomnymi), a nazwa i PID trafiają do konsoli. Przed startem usuwany jest też `.next/dev/lock`.
- **Zębatka:** polecenie tworzenia, polecenie po utworzeniu, polecenie uruchomienia, instalacja zależności, port, zmienne środowiskowe i opcje Next.js jako przełączniki. Flagi, których dana wersja narzędzia nie zna, są pomijane automatycznie (wpis w konsoli).

## Linki i sesje logowania
Tryb prywatny: Chrome, Brave, Edge i Firefox w oknie prywatnym.
Opcja **Firefox z profilem w DevKit** trzyma sesje logowania w `DevKit/data/firefox-profile`, więc przenoszą się razem z repo. Zamykaj Firefoksa przed synchronizacją w GitHub Desktop. Chromium (Chrome, Edge, Brave) szyfruje ciasteczka kluczem przypisanym do użytkownika Windows, dlatego tą drogą sesji między komputerami przenieść się nie da.

## Bezpieczeństwo
Serwer słucha tylko na 127.0.0.1, na losowym porcie, i wymaga tokenu generowanego przy każdym starcie.
Hasła i sesje trzymasz w repo, więc repo ma być prywatne.

## Claude (OmniRoute)
Zakładka **Claude** to czat z modelem przez OmniRoute (lub inne API zgodne z OpenAI). Jedna rozmowa na projekt, historia w `DevKit/data/claude-chat.json`.
1. Uruchom OmniRoute i skopiuj klucz z jego dashboardu (strona Endpoint). Domyślny adres to `http://localhost:20128/v1`.
2. Ustawienia > Claude: wpisz adres i klucz, kliknij **Pobierz listę modeli**, wybierz model (najlepiej z `claude` w nazwie) i zapisz.
3. Claude ma narzędzia **tylko do odczytu**: lista folderów, czytanie plików, wyszukiwanie w repo. Nie zapisuje plików i nie uruchamia poleceń. Wyłączone z dostępu: `.env*`, `*.pem`/`*.key`, `node_modules`, `.git`, `.next`, `keys/` i cały folder `DevKit/data` (hasła, zrzuty baz).
4. Jeśli model nie obsługuje narzędzi, DevKit sam przełącza się na rozmowę bez dostępu do plików.
Historia i klucz API leżą w `data/`, więc repo musi być prywatne. Nie chcesz synchronizować rozmowy? Dopisz `data/claude-chat.json` do `.gitignore`.

## Podgląd bazy
Przycisk **Podgląd** (Baza danych) rysuje tabele i relacje jak Designer w phpMyAdmin na podstawie zapisanego zrzutu, więc nie wymaga XAMPP-a. Tabele przeciągasz (układ się zapamiętuje), a kliknięcie tabeli pokazuje po prawej jej dane (do 20 000 wierszy na tabelę) i strukturę. Ctrl + kółko myszy przybliża.

## XAMPP, dane, linki
- Gdy XAMPP nie jest zainstalowany, na górze zakładki Baza danych pojawia się czerwony pasek z linkiem **Zainstaluj**, który otwiera instalator wskazany w Ustawieniach (bez ścieżki szuka `xampp*.exe` w Pobranych).
- **Wyczyść dane** (Ustawienia) usuwa zrzuty baz i historię czatu, czyli reset pod nowy projekt.
- Kolejność linków zmieniasz przeciąganiem uchwytu albo strzałkami.
