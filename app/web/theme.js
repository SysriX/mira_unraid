/* MIRA – Theme sofort beim Laden setzen (vor dem ersten Zeichnen, damit nichts hell aufblitzt). */
(function () {
  "use strict";
  var THEMES = [
    ["konsole", "Konsole · orange", "konsole"],
    ["konsole-gruen", "Konsole · grün", "konsole"],
    ["konsole-blau", "Konsole · blau", "konsole"],
    ["dunkel", "Dunkel · schlicht", "flach"],
    ["hell", "Hell", "flach"],
    ["system", "Wie das System", ""],
  ];
  var SCHLUESSEL = "mira.theme", STANDARD = "konsole";
  function lesen() { try { return localStorage.getItem(SCHLUESSEL) || STANDARD; } catch (e) { return STANDARD; } }
  function anwenden(wahl) {
    var t = THEMES.filter(function (x) { return x[0] === wahl; })[0] || THEMES[0];
    var name = t[0];
    if (name === "system") name = window.matchMedia && matchMedia("(prefers-color-scheme: light)").matches ? "hell" : "konsole";
    var stil = (THEMES.filter(function (x) { return x[0] === name; })[0] || THEMES[0])[2];
    var r = document.documentElement;
    r.setAttribute("data-theme", name);
    r.setAttribute("data-stil", stil);
  }
  window.MIRA_THEMES = THEMES;
  window.themeAktuell = lesen;
  window.themeSetzen = function (wahl) { try { localStorage.setItem(SCHLUESSEL, wahl); } catch (e) { /* privat */ } anwenden(wahl); };
  if (window.matchMedia) matchMedia("(prefers-color-scheme: light)").addEventListener("change", function () { if (lesen() === "system") anwenden("system"); });
  anwenden(lesen());
})();
