-- A patient with no date of birth on file is asked their age once, at sign-in. The age is kept together with
-- the day it was given, so it keeps counting up correctly (age now = reported age + whole years since then).
-- A date of birth, when known, always wins.
ALTER TABLE patients ADD COLUMN reported_age INTEGER CHECK (reported_age IS NULL OR reported_age BETWEEN 1 AND 120);
ALTER TABLE patients ADD COLUMN reported_age_on TEXT;
