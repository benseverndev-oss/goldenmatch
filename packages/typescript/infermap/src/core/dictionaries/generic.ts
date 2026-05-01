// Generic domain dictionary — PII, customer, order, common system fields.
// Mirrors infermap/dictionaries/generic.yaml.
export const DOMAIN: Record<string, readonly string[]> = {
  first_name: ["fname", "first", "given_name", "first_nm", "forename"],
  last_name: ["lname", "last", "surname", "family_name", "last_nm"],
  email: ["email_address", "e_mail", "email_addr", "mail", "contact_email"],
  phone: ["phone_number", "ph", "telephone", "tel", "mobile", "cell"],
  address: [
    "addr",
    "street_address",
    "addr_line_1",
    "address_line_1",
    "mailing_address",
  ],
  city: ["town", "municipality"],
  state: ["st", "province", "region"],
  zip: ["zipcode", "zip_code", "postal_code", "postal", "postcode"],
  name: [
    "full_name",
    "fullname",
    "customer_name",
    "display_name",
    "contact_name",
  ],
  company: [
    "organization",
    "org",
    "business",
    "employer",
    "firm",
    "company_name",
  ],
  dob: ["date_of_birth", "birth_date", "birthdate", "birthday"],
  country: ["nation", "country_code"],
  gender: ["sex"],
  id: ["identifier", "record_id", "uid"],
  created_at: ["signup_date", "create_date", "date_created"],
};
