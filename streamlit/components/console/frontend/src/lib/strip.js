/* Layout of the Overview layer health strip. Prototype D drew three cards in a 1fr 28px 1fr 28px 1fr grid, a chevron
   between each. 1 to 3 layers render exactly that. With 4 or more, the cards keep their 3-per-row width and wrap onto
   further rows (scrolling sideways would clip each card's hover popover); a row starts without a chevron. */
export const STRIP_PER_ROW = 3;
export const stripClass = n => (n > STRIP_PER_ROW ? 'health many' : 'health');
export const stripJoin = (i, n) => i > 0 && (n <= STRIP_PER_ROW || i % STRIP_PER_ROW !== 0);
