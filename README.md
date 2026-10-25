# nve
Data om strømsituasjonen i Norge

## Strømpriser

`docs/prices.json` inneholder ukesnitt av day-ahead-områdepriser (EUR/MWh) for NO1–NO5,
hentet fra [ENTSO-E Transparency Platform](https://transparency.entsoe.eu/) av
`scripts/hent_priser.py`. Workflowen *Oppdater strømpriser* kjører daglig og krever
repo-secreten `ENTSOE_TOKEN`. Kjør den manuelt med «full» avkrysset for å hente hele
historikken på nytt.
