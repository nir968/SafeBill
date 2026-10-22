SELECT * FROM bills;


SELECT * FROM bills
WHERE institution LIKE '%חשמל%';

SELECT * FROM bills
WHERE institution LIKE '%הוט%';

SELECT institution, amount, due_date 
FROM bills 
WHERE user_id = 1
ORDER BY amount DESC;

SELECT SUM(amount) AS total_spent
FROM bills 
WHERE user_id = 1;

SELECT institution, amount, due_date 
FROM bills 
WHERE due_date < CURRENT_DATE;


