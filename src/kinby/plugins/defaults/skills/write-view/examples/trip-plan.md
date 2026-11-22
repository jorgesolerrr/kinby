# A page made once

A plan for a long weekend, written on one turn and read a few times. No routine refreshes it, so
everything lives in the page, the data is empty, and it needs no script.

It is read, not scanned, so it breathes: prose at 1rem, a measure near 65 characters, and days
set apart by space and a heading instead of boxes. Times sit in a narrow column of tabular
numerals so the day reads down its left edge. The bookings share their fields, so they are a
small table. Links lead to the places and the bookings, and colour appears only on them.

## Page

```html
<style>
  .plan {
    max-width: 42rem;
  }
  .lede {
    margin-bottom: 2rem;
    color: var(--muted-foreground);
  }
  h2 {
    margin: 2rem 0 0;
  }
  .theme {
    margin: 0 0 0.75rem;
    color: var(--muted-foreground);
    font-size: 0.875rem;
  }
  ol {
    margin: 0;
    padding: 0;
    list-style: none;
  }
  li {
    display: grid;
    grid-template-columns: 3.5rem 1fr;
    gap: 0.75rem;
    padding: 0.375rem 0;
  }
  time {
    color: var(--muted-foreground);
    font-size: 0.875rem;
    font-variant-numeric: tabular-nums;
    line-height: 1.7;
  }
  .detail {
    display: block;
    color: var(--muted-foreground);
    font-size: 0.875rem;
  }
  table {
    font-size: 0.875rem;
  }
  th {
    font-size: 0.75rem;
  }
  th:first-child,
  td:first-child {
    padding-left: 0;
  }
  td + td {
    white-space: nowrap;
  }
  code {
    font-size: 0.8125rem;
  }
</style>

<div class="plan">
  <p class="lede">
    Lisbon, Friday 4 to Monday 7 December. Flights land Friday at noon and leave Monday at 16:40, so
    the plan keeps Monday light and close to the airport line.
  </p>

  <h2>Friday 4</h2>
  <p class="theme">Arrive, and Alfama on foot</p>
  <ol>
    <li>
      <time>12:05</time>
      <span>Land at Humberto Delgado. Red metro line to Saldanha, then green to Baixa-Chiado.
        <span class="detail">About 35 minutes. Tap a contactless card at the gate.</span></span>
    </li>
    <li>
      <time>14:00</time>
      <span>Check in at <a href="https://hotel.example/casa-do-largo">Casa do Largo</a>, Rua
        de São Tomé 12.</span>
    </li>
    <li>
      <time>15:30</time>
      <span>Walk up through Alfama to the
        <a href="https://maps.example/miradouro-santa-luzia">Miradouro de Santa Luzia</a>.
        <span class="detail">Steep. The 28 tram is the lazy way, but it is full by now.</span></span>
    </li>
    <li>
      <time>20:00</time>
      <span>Dinner at Taberna da Rua das Flores.
        <span class="detail">No bookings: arrive before eight or expect a wait.</span></span>
    </li>
  </ol>

  <h2>Saturday 5</h2>
  <p class="theme">Belém, and the river</p>
  <ol>
    <li>
      <time>09:30</time>
      <span>Tram 15 from Praça da Figueira to Belém.</span>
    </li>
    <li>
      <time>10:00</time>
      <span><a href="https://tickets.example/jeronimos">Jerónimos Monastery</a>, booked.
        <span class="detail">The cloister is the part worth the queue.</span></span>
    </li>
    <li>
      <time>12:30</time>
      <span>Pastéis de Belém, then lunch by the river at the MAAT.</span>
    </li>
    <li>
      <time>21:00</time>
      <span>Fado at <a href="https://tickets.example/mesa-de-frades">Mesa de Frades</a>,
        booked for two.</span>
    </li>
  </ol>

  <h2>Sunday 6</h2>
  <p class="theme">Sintra</p>
  <ol>
    <li>
      <time>08:40</time>
      <span>Train from Rossio to Sintra, 40 minutes.</span>
    </li>
    <li>
      <time>10:00</time>
      <span><a href="https://tickets.example/pena">Pena Palace</a>, timed entry.
        <span class="detail">Take the 434 bus up. Walk down through the park.</span></span>
    </li>
    <li>
      <time>14:00</time>
      <span>Quinta da Regaleira, if the legs allow.</span>
    </li>
  </ol>

  <h2>Monday 7</h2>
  <p class="theme">A slow morning</p>
  <ol>
    <li>
      <time>10:00</time>
      <span>Coffee and the market at Time Out Mercado da Ribeira.</span>
    </li>
    <li>
      <time>14:00</time>
      <span>Metro to the airport. Check-in for the 16:40 closes at 15:55.</span>
    </li>
  </ol>

  <h2>Bookings</h2>
  <table>
    <thead>
      <tr>
        <th>What</th>
        <th>When</th>
        <th>Reference</th>
      </tr>
    </thead>
    <tbody>
      <tr>
        <td><a href="https://hotel.example/casa-do-largo">Casa do Largo</a>, three nights</td>
        <td>Fri to Mon</td>
        <td><code>CDL-48213</code></td>
      </tr>
      <tr>
        <td><a href="https://tickets.example/jeronimos">Jerónimos Monastery</a></td>
        <td>Sat 10:00</td>
        <td><code>JM-7Q2K</code></td>
      </tr>
      <tr>
        <td><a href="https://tickets.example/mesa-de-frades">Mesa de Frades</a></td>
        <td>Sat 21:00</td>
        <td><code>MF-0912</code></td>
      </tr>
      <tr>
        <td><a href="https://tickets.example/pena">Pena Palace</a></td>
        <td>Sun 10:00</td>
        <td><code>PN-55310</code></td>
      </tr>
    </tbody>
  </table>
</div>
```

## Data

```json
{}
```
