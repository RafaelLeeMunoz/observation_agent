# Unallocated or Unmatched Meter Volume: Exception Investigation Checklist

Use this checklist when a meter shows unallocated volume, volume sitting with the wrong party, or volume with no covering deal. It helps the logistics analyst gather the facts once, send one complete request, and track the answer. The outcome is the meter or volumes reallocated, or a deal assigned, or a write-off.

## 1. Pin down the exception
- [ ] Meter number: [meter #]
- [ ] Production month and days affected: [month, days, e.g. days 1-20 vs. 21-31] (a deal can cover only part of a month [Q20][Q12][Q13])
- [ ] Volume at issue: [volume]
- [ ] Check whether the meter is non-allocatable or off system, and which meter holds the actuals [Q6]
- [ ] Note the party the gas is currently allocated to, including any "stranger's gas" placeholder [Q15]

## 2. Trace the cause
- [ ] Check measurement for flow in the period, and whether anything shows the gas was received on the expected system [Q2]
- [ ] Look at the deal history in the system for the period and whether other deals exist [Q1]
- [ ] Ask whether a deal sits in [source system] that did not feed over to [target system] [Q4]
- [ ] Check the dates the existing deal is valid for against the days with volume [Q20]
- [ ] Note anything odd, such as flow in only one month, and say so in the request [Q3]
- [ ] Check whether an OBA is in place or still to arrive from the pipeline [Q8][Q9]

## 3. Send one complete request
- [ ] Send to [deal owner / volume management / settlements contact], copying [others who have worked the meter]
- [ ] Include meter, days, volume, and candidate deals: [candidate deal numbers] [Q10][Q19]
- [ ] State the specific ask: [a deal number to place the days on / OK to apply volumes / activate days in the deal system] [Q10][Q16][Q7]
- [ ] Say what has already been checked, so nobody repeats it [Q2][Q6]

## 4. Follow up
- [ ] Record the date sent and a follow-up date: [date]
- [ ] If there is no answer, chase with the open question restated. The same issue has come back more than once [Q11][Q13][Q15][Q14][Q18]
- [ ] If the answer is "will respond later" or depends on a renegotiation, log the owner and the promised date [Q21][Q22]
- [ ] If a prior request is still open, re-ask it [Q23][Q24]

## 5. Close out
- [ ] Reallocate the meter or apply the volumes, and confirm to the requester [Q17]
- [ ] Record the deal number assigned, or the write-off decision: [deal # / write-off]
- [ ] Confirm that all days in the month are covered, not just part [Q12][Q20]

## Why this helps
The quotes show the same facts being asked for repeatedly: which days a deal covers [Q20][Q13], who owns the gas for the uncovered days [Q12][Q10], and what measurement or deal records show [Q2][Q4]. Requests then sit open and are re-asked, with phrases like "any luck" and "back on this issue again" [Q14][Q11][Q15], or are waiting on another person's reply [Q22][Q24]. Collecting meter, days, volume and candidate deals up front, and setting a follow-up date, targets those stalls.

---

## Evidence (appended by code; quotes are verbatim from the raw files)

- **[Q1]** `corpus/farmer-d/logistics/622.` bytes 894-961 | 1999-12-28: "OSS for the whole year of 1998 and there are no other deals in OSS."
- **[Q2]** `corpus/farmer-d/logistics/622.` bytes 560-712 | 1999-12-28: "Revision - According to measurement, there is flow of 7,394 in 8/98, however, there is nothing to indicate this is gas that was received on HPL in OSS."
- **[Q3]** `corpus/farmer-d/logistics/622.` bytes 963-1021 | 1999-12-28: "It does seem strange that this is the only month of flow."
- **[Q4]** `corpus/farmer-d/logistics/622.` bytes 816-880 | 1999-12-28: "Daren is there a deal in CPR which may not have fed over to OSS?"
- **[Q6]** `corpus/farmer-d/logistics/670.` bytes 1154-1232 | 2000-01-11: "The above meter is non-allocatable and was off system, actuals at meter #8693."
- **[Q7]** `corpus/farmer-d/logistics/995.` bytes 639-715 | 2000-03-31: "Can you see if it is feasible to activate these two days in Sitara so that I"
- **[Q8]** `corpus/farmer-d/logistics/1000.` bytes 474-550 | 2000-03-31: "Ray Calles (TETCO) told me today that he will put the OBA in the mail either"
- **[Q9]** `corpus/farmer-d/logistics/1000.` bytes 749-788 | 2000-03-31: "Don't we have an OBA at this point now?"
- **[Q10]** `corpus/farmer-d/logistics/1149.` bytes 691-762 | 2000-05-26: "Can you assist Anita and I w/a deal number to place the 1st 20 days on."
- **[Q11]** `corpus/farmer-d/logistics/1149.` bytes 573-604 | 2000-05-26: "We're back on this issue again."
- **[Q12]** `corpus/farmer-d/logistics/1149.` bytes 1453-1518 | 2000-04-10: "don't we have to find out who this gas belong's to for days 1-20?"
- **[Q13]** `corpus/farmer-d/logistics/1149.` bytes 1364-1518 | 2000-04-10: "Jackie, We are back on this issue again...........the deal was only good for day 21-31, don't we have to find out who this gas belong's to for days 1-20?"
- **[Q14]** `corpus/farmer-d/logistics/1149.` bytes 2937-2990 | 2000-04-03: "Jackie, any luck on the issue w/ the gas allocated to"
- **[Q15]** `corpus/farmer-d/logistics/1149.` bytes 2937-3032 | 2000-04-03: "Jackie, any luck on the issue w/ the gas allocated to "stranger's gas" at this meter.........."
- **[Q16]** `corpus/farmer-d/logistics/1149.` bytes 2483-2546 | 2000-04-03: "Is it o.k. for Volume Management to apply volumes from the 21st"
- **[Q17]** `corpus/farmer-d/logistics/1149.` bytes 1739-1770 | 2000-04-03: "I've just reallocated the meter"
- **[Q18]** `corpus/farmer-d/logistics/1149.` bytes 3330-3397 | 2000-04-03: "Anita, have you found out any further information about this meter."
- **[Q19]** `corpus/farmer-d/logistics/1149.` bytes 4247-4322 | 2000-03-03: "For meter # 0986725, 1/00 production, I am showing per meter statement that"
- **[Q20]** `corpus/farmer-d/logistics/1149.` bytes 3828-3875 | 2000-03-03: "deal 152638 is only valid from the 21st - 31st."
- **[Q21]** `corpus/farmer-d/logistics/1152.` bytes 648-720 | 2000-05-26: "re-negotiate the deal and Daren will respond to me on Tuesday next week."
- **[Q22]** `corpus/farmer-d/logistics/1152.` bytes 570-720 | 2000-05-26: "I spoke w/Daren and he apprised that he e-mailed Craig and that Craig has to re-negotiate the deal and Daren will respond to me on Tuesday next week."
- **[Q23]** `corpus/farmer-d/logistics/1156.` bytes 575-651 | 2000-05-30: "Have you anything about 98-6725 for 1/2000 production for the 1st 20 days of"
- **[Q24]** `corpus/farmer-d/logistics/1156.` bytes 575-667 | 2000-05-30: "Have you anything about 98-6725 for 1/2000 production for the 1st 20 days of the month yet?"

*Draft generated for process P4; never sent. Packet a001 sha256 65c0c467f1bf.*
