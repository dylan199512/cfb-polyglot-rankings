IDENTIFICATION DIVISION.
       PROGRAM-ID. ELO-ENGINE.
       AUTHOR. POLYGLOT-CFB-RANKINGS.

       ENVIRONMENT DIVISION.
       INPUT-OUTPUT SECTION.
       FILE-CONTROL.
           SELECT GAMES-FILE ASSIGN TO "games.dat"
               ORGANIZATION IS LINE SEQUENTIAL.
           SELECT RATINGS-FILE ASSIGN TO "elo_ratings.dat"
               ORGANIZATION IS LINE SEQUENTIAL.
           SELECT REPORT-FILE ASSIGN TO "rankings_report.txt"
               ORGANIZATION IS LINE SEQUENTIAL.

       DATA DIVISION.
       FILE SECTION.
       FD  GAMES-FILE.
       01  GAME-RECORD.
           05 IN-GAME-ID          PIC 9(04).
           05 IN-WEEK             PIC 9(02).
           05 IN-HOME-TEAM        PIC X(25).
           05 IN-HOME-DIV         PIC X(01).
           05 IN-HOME-SCORE       PIC 9(03).
           05 IN-AWAY-TEAM        PIC X(25).
           05 IN-AWAY-DIV         PIC X(01).
           05 IN-AWAY-SCORE       PIC 9(03).
           05 IN-NEUTRAL          PIC X(01).

       FD  RATINGS-FILE.
       01  RATING-RECORD.
           05 OUT-TEAM            PIC X(25).
           05 OUT-DIV             PIC X(01).
           05 OUT-GAMES           PIC 9(03).
           05 OUT-WINS            PIC 9(03).
           05 OUT-LOSSES          PIC 9(03).
           05 OUT-ELO             PIC 9(04).9(04).

       FD  REPORT-FILE.
       01  REPORT-LINE            PIC X(132).

       WORKING-STORAGE SECTION.
       01  WS-EOF                 PIC X(01) VALUE "N".
       01  WS-GAME-COUNT          PIC 9(04) VALUE 0.
       01  WS-TEAM-COUNT          PIC 9(03) VALUE 0.
       01  WS-IDX                 PIC 9(03) VALUE 0.
       01  WS-H-IDX               PIC 9(03) VALUE 0.
       01  WS-A-IDX               PIC 9(03) VALUE 0.

       01  TEAM-TABLE.
           05 TEAM-ENTRY OCCURS 350 TIMES.
              10 TB-NAME          PIC X(25).
              10 TB-DIV           PIC X(01).
              10 TB-GAMES         PIC 9(03) COMP-3.
              10 TB-WINS          PIC 9(03) COMP-3.
              10 TB-LOSSES        PIC 9(03) COMP-3.
              10 TB-ELO           PIC S9(05)V9(04) COMP-3.

       01  WS-CALC-VARS.
           05 WS-H-PRE            PIC S9(05)V9(04) COMP-3.
           05 WS-A-PRE            PIC S9(05)V9(04) COMP-3.
           05 WS-HFA              PIC S9(04)V9(04) COMP-3.
           05 WS-DIFF             COMP-2.
           05 WS-EXP-HOME         COMP-2.
           05 WS-ACTUAL-HOME      COMP-2.
           05 WS-MARGIN           PIC S9(04) COMP-3.
           05 WS-WIN-DIFF         COMP-2.
           05 WS-MOV-MULT         COMP-2.
           05 WS-K-FACTOR         COMP-2.
           05 WS-SHIFT-FLT        COMP-2.
           05 WS-SHIFT            PIC S9(05)V9(04) COMP-3.
           05 WS-TAG              PIC X(03).

       01  RPT-HEADER-1.
           05 FILLER              PIC X(54) VALUE
             "2026 COBOL FIXED-POINT (COMP-3) ELO VOLATILITY LEDGER ".
           05 FILLER              PIC X(78) VALUE
             "(TIERS: P4=1560, G6=1440, FCS=1200 | NOV K-BOOST: 1.25X)".

       01  RPT-HEADER-2.
           05 FILLER              PIC X(48) VALUE
              "GAME WK  HOME TEAM                 TIER SCR     ".
           05 FILLER              PIC X(48) VALUE
              "AWAY TEAM                 TIER SCR  MODE   PRE-H".
           05 FILLER              PIC X(36) VALUE
              "     PRE-A     DELTA    POST-H".

       01  RPT-DETAIL.
           05 RPT-ID              PIC 9(04).
           05 FILLER              PIC X(01) VALUE SPACE.
           05 RPT-WK              PIC 9(02).
           05 FILLER              PIC X(02) VALUE SPACES.
           05 RPT-HOME            PIC X(25).
           05 FILLER              PIC X(01) VALUE SPACE.
           05 RPT-HDIV            PIC X(01).
           05 FILLER              PIC X(04) VALUE SPACES.
           05 RPT-HSCR            PIC 9(03).
           05 FILLER              PIC X(05) VALUE SPACES.
           05 RPT-AWAY            PIC X(25).
           05 FILLER              PIC X(01) VALUE SPACE.
           05 RPT-ADIV            PIC X(01).
           05 FILLER              PIC X(04) VALUE SPACES.
           05 RPT-ASCR            PIC 9(03).
           05 FILLER              PIC X(02) VALUE SPACES.
           05 RPT-MODE            PIC X(03).
           05 FILLER              PIC X(02) VALUE SPACES.
           05 RPT-PREH            PIC 9(04).9(02).
           05 FILLER              PIC X(02) VALUE SPACES.
           05 RPT-PREA            PIC 9(04).9(02).
           05 FILLER              PIC X(02) VALUE SPACES.
           05 RPT-DELTA           PIC +9(03).9(02).
           05 FILLER              PIC X(02) VALUE SPACES.
           05 RPT-POSTH           PIC 9(04).9(02).

       PROCEDURE DIVISION.
       MAIN-PARA.
           OPEN INPUT GAMES-FILE
                OUTPUT RATINGS-FILE
                OUTPUT REPORT-FILE.

           WRITE REPORT-LINE FROM RPT-HEADER-1.
           MOVE ALL "-" TO REPORT-LINE.
           WRITE REPORT-LINE.
           WRITE REPORT-LINE FROM RPT-HEADER-2.
           MOVE ALL "-" TO REPORT-LINE.
           WRITE REPORT-LINE.

           PERFORM UNTIL WS-EOF = "Y"
               READ GAMES-FILE
                   AT END
                       MOVE "Y" TO WS-EOF
                   NOT AT END
                       ADD 1 TO WS-GAME-COUNT
                       PERFORM PROCESS-GAME
               END-READ
           END-PERFORM.

           PERFORM WRITE-RATINGS-LEDGER.

           CLOSE GAMES-FILE RATINGS-FILE REPORT-FILE.

           DISPLAY "COBOL ELO LEDGER COMPLETE. GAMES: "
                   WS-GAME-COUNT " | TEAMS: " WS-TEAM-COUNT.
           STOP RUN.

       PROCESS-GAME.
           PERFORM FIND-OR-ADD-HOME.
           PERFORM FIND-OR-ADD-AWAY.

           MOVE TB-ELO(WS-H-IDX) TO WS-H-PRE.
           MOVE TB-ELO(WS-A-IDX) TO WS-A-PRE.

           IF IN-NEUTRAL = "Y"
               MOVE 0.0000 TO WS-HFA
           ELSE
               MOVE 55.0000 TO WS-HFA
           END-IF.

           COMPUTE WS-DIFF = (WS-H-PRE + WS-HFA) - WS-A-PRE.
           COMPUTE WS-EXP-HOME =
               1.0 / (1.0 + (10.0 ** (-WS-DIFF / 400.0))).

           IF IN-HOME-SCORE > IN-AWAY-SCORE
               MOVE 1.0 TO WS-ACTUAL-HOME
               COMPUTE WS-MARGIN = IN-HOME-SCORE - IN-AWAY-SCORE
               COMPUTE WS-WIN-DIFF = (WS-H-PRE + WS-HFA) - WS-A-PRE
               ADD 1 TO TB-WINS(WS-H-IDX)
               ADD 1 TO TB-LOSSES(WS-A-IDX)
           ELSE
               MOVE 0.0 TO WS-ACTUAL-HOME
               COMPUTE WS-MARGIN = IN-AWAY-SCORE - IN-HOME-SCORE
               COMPUTE WS-WIN-DIFF = WS-A-PRE - (WS-H-PRE + WS-HFA)
               ADD 1 TO TB-WINS(WS-A-IDX)
               ADD 1 TO TB-LOSSES(WS-H-IDX)
           END-IF.

           ADD 1 TO TB-GAMES(WS-H-IDX).
           ADD 1 TO TB-GAMES(WS-A-IDX).

           IF WS-WIN-DIFF < -1000.0
               MOVE -1000.0 TO WS-WIN-DIFF
           END-IF.

           COMPUTE WS-MOV-MULT =
               FUNCTION LOG(FUNCTION ABS(WS-MARGIN) + 1.0) *
               (2.2 / ((WS-WIN-DIFF * 0.001) + 2.2)).

           IF IN-WEEK >= 9
               MOVE 40.0 TO WS-K-FACTOR
               MOVE "NOV" TO WS-TAG
           ELSE
               MOVE 32.0 TO WS-K-FACTOR
               MOVE "REG" TO WS-TAG
           END-IF.

           COMPUTE WS-SHIFT-FLT =
               WS-K-FACTOR * WS-MOV-MULT *
               (WS-ACTUAL-HOME - WS-EXP-HOME).
           MOVE WS-SHIFT-FLT TO WS-SHIFT.

           ADD WS-SHIFT TO TB-ELO(WS-H-IDX).
           SUBTRACT WS-SHIFT FROM TB-ELO(WS-A-IDX).

           MOVE IN-GAME-ID        TO RPT-ID.
           MOVE IN-WEEK           TO RPT-WK.
           MOVE IN-HOME-TEAM      TO RPT-HOME.
           MOVE IN-HOME-DIV       TO RPT-HDIV.
           MOVE IN-HOME-SCORE     TO RPT-HSCR.
           MOVE IN-AWAY-TEAM      TO RPT-AWAY.
           MOVE IN-AWAY-DIV       TO RPT-ADIV.
           MOVE IN-AWAY-SCORE     TO RPT-ASCR.
           MOVE WS-TAG            TO RPT-MODE.
           MOVE WS-H-PRE          TO RPT-PREH.
           MOVE WS-A-PRE          TO RPT-PREA.
           MOVE WS-SHIFT          TO RPT-DELTA.
           MOVE TB-ELO(WS-H-IDX)  TO RPT-POSTH.
           WRITE REPORT-LINE FROM RPT-DETAIL.

       FIND-OR-ADD-HOME.
           MOVE 0 TO WS-H-IDX.
           PERFORM VARYING WS-IDX FROM 1 BY 1
               UNTIL WS-IDX > WS-TEAM-COUNT OR WS-H-IDX > 0
               IF TB-NAME(WS-IDX) = IN-HOME-TEAM
                   MOVE WS-IDX TO WS-H-IDX
               END-IF
           END-PERFORM.
           IF WS-H-IDX = 0
               ADD 1 TO WS-TEAM-COUNT
               MOVE WS-TEAM-COUNT TO WS-H-IDX
               MOVE IN-HOME-TEAM TO TB-NAME(WS-H-IDX)
               MOVE IN-HOME-DIV  TO TB-DIV(WS-H-IDX)
               MOVE 0            TO TB-GAMES(WS-H-IDX)
               MOVE 0            TO TB-WINS(WS-H-IDX)
               MOVE 0            TO TB-LOSSES(WS-H-IDX)
               EVALUATE IN-HOME-DIV
                   WHEN "P"
                       MOVE 1560.0000 TO TB-ELO(WS-H-IDX)
                   WHEN "G"
                       MOVE 1440.0000 TO TB-ELO(WS-H-IDX)
                   WHEN OTHER
                       MOVE 1200.0000 TO TB-ELO(WS-H-IDX)
               END-EVALUATE
           END-IF.

       FIND-OR-ADD-AWAY.
           MOVE 0 TO WS-A-IDX.
           PERFORM VARYING WS-IDX FROM 1 BY 1
               UNTIL WS-IDX > WS-TEAM-COUNT OR WS-A-IDX > 0
               IF TB-NAME(WS-IDX) = IN-AWAY-TEAM
                   MOVE WS-IDX TO WS-A-IDX
               END-IF
           END-PERFORM.
           IF WS-A-IDX = 0
               ADD 1 TO WS-TEAM-COUNT
               MOVE WS-TEAM-COUNT TO WS-A-IDX
               MOVE IN-AWAY-TEAM TO TB-NAME(WS-A-IDX)
               MOVE IN-AWAY-DIV  TO TB-DIV(WS-A-IDX)
               MOVE 0            TO TB-GAMES(WS-A-IDX)
               MOVE 0            TO TB-WINS(WS-A-IDX)
               MOVE 0            TO TB-LOSSES(WS-A-IDX)
               EVALUATE IN-AWAY-DIV
                   WHEN "P"
                       MOVE 1560.0000 TO TB-ELO(WS-A-IDX)
                   WHEN "G"
                       MOVE 1440.0000 TO TB-ELO(WS-A-IDX)
                   WHEN OTHER
                       MOVE 1200.0000 TO TB-ELO(WS-A-IDX)
               END-EVALUATE
           END-IF.

       WRITE-RATINGS-LEDGER.
           PERFORM VARYING WS-IDX FROM 1 BY 1
               UNTIL WS-IDX > WS-TEAM-COUNT
               INITIALIZE RATING-RECORD
               MOVE TB-NAME(WS-IDX)   TO OUT-TEAM
               MOVE TB-DIV(WS-IDX)    TO OUT-DIV
               MOVE TB-GAMES(WS-IDX)  TO OUT-GAMES
               MOVE TB-WINS(WS-IDX)   TO OUT-WINS
               MOVE TB-LOSSES(WS-IDX) TO OUT-LOSSES
               MOVE TB-ELO(WS-IDX)    TO OUT-ELO
               WRITE RATING-RECORD
           END-PERFORM.