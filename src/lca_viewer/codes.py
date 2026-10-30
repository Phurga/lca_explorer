"""Names for the codes of the filter fields: locations (ISO 3166 countries, ecoinvent regions, subdivisions),
ISIC Rev. 4 divisions and CPC sections. The names of ISIC classes and CPC classes are in the data itself
(``"4322:Plumbing, heat and air-conditioning installation"``)."""

from __future__ import annotations

import re


def _table(text: str) -> dict[str, str]:
    """``"CODE Name"`` lines to a dict."""
    return dict(line.split(" ", 1) for line in text.strip().splitlines())


COUNTRIES = _table("""
AD Andorra
AE United Arab Emirates
AF Afghanistan
AG Antigua and Barbuda
AI Anguilla
AL Albania
AM Armenia
AO Angola
AQ Antarctica
AR Argentina
AS American Samoa
AT Austria
AU Australia
AW Aruba
AX Åland Islands
AZ Azerbaijan
BA Bosnia and Herzegovina
BB Barbados
BD Bangladesh
BE Belgium
BF Burkina Faso
BG Bulgaria
BH Bahrain
BI Burundi
BJ Benin
BL Saint Barthélemy
BM Bermuda
BN Brunei Darussalam
BO Bolivia
BQ Bonaire, Sint Eustatius and Saba
BR Brazil
BS Bahamas
BT Bhutan
BV Bouvet Island
BW Botswana
BY Belarus
BZ Belize
CA Canada
CC Cocos (Keeling) Islands
CD Congo, Democratic Republic of the
CF Central African Republic
CG Congo
CH Switzerland
CI Côte d'Ivoire
CK Cook Islands
CL Chile
CM Cameroon
CN China
CO Colombia
CR Costa Rica
CU Cuba
CV Cabo Verde
CW Curaçao
CX Christmas Island
CY Cyprus
CZ Czechia
DE Germany
DJ Djibouti
DK Denmark
DM Dominica
DO Dominican Republic
DZ Algeria
EC Ecuador
EE Estonia
EG Egypt
EH Western Sahara
ER Eritrea
ES Spain
ET Ethiopia
FI Finland
FJ Fiji
FK Falkland Islands
FM Micronesia
FO Faroe Islands
FR France
GA Gabon
GB United Kingdom
GD Grenada
GE Georgia
GF French Guiana
GG Guernsey
GH Ghana
GI Gibraltar
GL Greenland
GM Gambia
GN Guinea
GP Guadeloupe
GQ Equatorial Guinea
GR Greece
GS South Georgia and the South Sandwich Islands
GT Guatemala
GU Guam
GW Guinea-Bissau
GY Guyana
HK Hong Kong
HM Heard Island and McDonald Islands
HN Honduras
HR Croatia
HT Haiti
HU Hungary
ID Indonesia
IE Ireland
IL Israel
IM Isle of Man
IN India
IO British Indian Ocean Territory
IQ Iraq
IR Iran
IS Iceland
IT Italy
JE Jersey
JM Jamaica
JO Jordan
JP Japan
KE Kenya
KG Kyrgyzstan
KH Cambodia
KI Kiribati
KM Comoros
KN Saint Kitts and Nevis
KP Korea, Democratic People's Republic of
KR Korea, Republic of
KW Kuwait
KY Cayman Islands
KZ Kazakhstan
LA Lao People's Democratic Republic
LB Lebanon
LC Saint Lucia
LI Liechtenstein
LK Sri Lanka
LR Liberia
LS Lesotho
LT Lithuania
LU Luxembourg
LV Latvia
LY Libya
MA Morocco
MC Monaco
MD Moldova
ME Montenegro
MF Saint Martin (French part)
MG Madagascar
MH Marshall Islands
MK North Macedonia
ML Mali
MM Myanmar
MN Mongolia
MO Macao
MP Northern Mariana Islands
MQ Martinique
MR Mauritania
MS Montserrat
MT Malta
MU Mauritius
MV Maldives
MW Malawi
MX Mexico
MY Malaysia
MZ Mozambique
NA Namibia
NC New Caledonia
NE Niger
NF Norfolk Island
NG Nigeria
NI Nicaragua
NL Netherlands
NO Norway
NP Nepal
NR Nauru
NU Niue
NZ New Zealand
OM Oman
PA Panama
PE Peru
PF French Polynesia
PG Papua New Guinea
PH Philippines
PK Pakistan
PL Poland
PM Saint Pierre and Miquelon
PN Pitcairn
PR Puerto Rico
PS Palestine
PT Portugal
PW Palau
PY Paraguay
QA Qatar
RE Réunion
RO Romania
RS Serbia
RU Russian Federation
RW Rwanda
SA Saudi Arabia
SB Solomon Islands
SC Seychelles
SD Sudan
SE Sweden
SG Singapore
SH Saint Helena, Ascension and Tristan da Cunha
SI Slovenia
SJ Svalbard and Jan Mayen
SK Slovakia
SL Sierra Leone
SM San Marino
SN Senegal
SO Somalia
SR Suriname
SS South Sudan
ST Sao Tome and Principe
SV El Salvador
SX Sint Maarten (Dutch part)
SY Syrian Arab Republic
SZ Eswatini
TC Turks and Caicos Islands
TD Chad
TF French Southern Territories
TG Togo
TH Thailand
TJ Tajikistan
TK Tokelau
TL Timor-Leste
TM Turkmenistan
TN Tunisia
TO Tonga
TR Türkiye
TT Trinidad and Tobago
TV Tuvalu
TW Taiwan
TZ Tanzania
UA Ukraine
UG Uganda
UM United States Minor Outlying Islands
US United States
UY Uruguay
UZ Uzbekistan
VA Holy See
VC Saint Vincent and the Grenadines
VE Venezuela
VG Virgin Islands, British
VI Virgin Islands, U.S.
VN Viet Nam
VU Vanuatu
WF Wallis and Futuna
WS Samoa
XK Kosovo
YE Yemen
YT Mayotte
ZA South Africa
ZM Zambia
ZW Zimbabwe
""")

# Codes of ecoinvent that are not countries (others, such as "Europe without Switzerland", read as they are)
REGIONS = _table("""
GLO Global
RoW Rest of the world
RER Europe
RAS Asia
RAF Africa
RLA Latin America and the Caribbean
RNA North America
RME Middle East
""")

# Subdivisions written ``<country>-<code>`` (ecoinvent: states, provinces)
SUBDIVISIONS: dict[str, dict[str, str]] = {
    "US": _table("""
AL Alabama
AK Alaska
AZ Arizona
AR Arkansas
CA California
CO Colorado
CT Connecticut
DE Delaware
DC District of Columbia
FL Florida
GA Georgia
HI Hawaii
ID Idaho
IL Illinois
IN Indiana
IA Iowa
KS Kansas
KY Kentucky
LA Louisiana
ME Maine
MD Maryland
MA Massachusetts
MI Michigan
MN Minnesota
MS Mississippi
MO Missouri
MT Montana
NE Nebraska
NV Nevada
NH New Hampshire
NJ New Jersey
NM New Mexico
NY New York
NC North Carolina
ND North Dakota
OH Ohio
OK Oklahoma
OR Oregon
PA Pennsylvania
RI Rhode Island
SC South Carolina
SD South Dakota
TN Tennessee
TX Texas
UT Utah
VT Vermont
VA Virginia
WA Washington
WV West Virginia
WI Wisconsin
WY Wyoming
"""),
    "CA": _table("""
AB Alberta
BC British Columbia
MB Manitoba
NB New Brunswick
NL Newfoundland and Labrador
NS Nova Scotia
NT Northwest Territories
NU Nunavut
ON Ontario
PE Prince Edward Island
QC Québec
SK Saskatchewan
YT Yukon
"""),
    "BR": _table("""
AC Acre
AL Alagoas
AM Amazonas
AP Amapá
BA Bahia
CE Ceará
DF Distrito Federal
ES Espírito Santo
GO Goiás
MA Maranhão
MG Minas Gerais
MS Mato Grosso do Sul
MT Mato Grosso
PA Pará
PB Paraíba
PE Pernambuco
PI Piauí
PR Paraná
RJ Rio de Janeiro
RN Rio Grande do Norte
RO Rondônia
RR Roraima
RS Rio Grande do Sul
SC Santa Catarina
SE Sergipe
SP São Paulo
TO Tocantins
"""),
    "CN": _table("""
AH Anhui
BJ Beijing
CQ Chongqing
FJ Fujian
GD Guangdong
GS Gansu
GX Guangxi
GZ Guizhou
HA Henan
HB Hubei
HE Hebei
HI Hainan
HL Heilongjiang
HN Hunan
JL Jilin
JS Jiangsu
JX Jiangxi
LN Liaoning
NM Inner Mongolia
NX Ningxia
QH Qinghai
SC Sichuan
SD Shandong
SH Shanghai
SN Shaanxi
SX Shanxi
TJ Tianjin
XJ Xinjiang
XZ Tibet
YN Yunnan
ZJ Zhejiang
"""),
}


def location_name(code: str) -> str:
    """What a location code stands for, or "" when it reads as it is (``Europe without Switzerland``)."""
    if code in REGIONS:
        return REGIONS[code]
    if code in COUNTRIES:
        return COUNTRIES[code]
    m = re.fullmatch(r"([A-Z]{2})-(.+)", code)
    if m and m.group(1) in COUNTRIES:
        country, sub = m.groups()
        return f"{COUNTRIES[country]} – {SUBDIVISIONS.get(country, {}).get(sub, sub)}"
    if code.startswith("UN-"):
        return f"United Nations region {code[3:]}"
    return ""


ISIC_DIVISIONS = _table("""
01 Crop and animal production, hunting and related service activities
02 Forestry and logging
03 Fishing and aquaculture
05 Mining of coal and lignite
06 Extraction of crude petroleum and natural gas
07 Mining of metal ores
08 Other mining and quarrying
09 Mining support service activities
10 Manufacture of food products
11 Manufacture of beverages
12 Manufacture of tobacco products
13 Manufacture of textiles
14 Manufacture of wearing apparel
15 Manufacture of leather and related products
16 Manufacture of wood and of products of wood and cork, except furniture; manufacture of articles of straw and plaiting materials
17 Manufacture of paper and paper products
18 Printing and reproduction of recorded media
19 Manufacture of coke and refined petroleum products
20 Manufacture of chemicals and chemical products
21 Manufacture of pharmaceuticals, medicinal chemical and botanical products
22 Manufacture of rubber and plastics products
23 Manufacture of other non-metallic mineral products
24 Manufacture of basic metals
25 Manufacture of fabricated metal products, except machinery and equipment
26 Manufacture of computer, electronic and optical products
27 Manufacture of electrical equipment
28 Manufacture of machinery and equipment n.e.c.
29 Manufacture of motor vehicles, trailers and semi-trailers
30 Manufacture of other transport equipment
31 Manufacture of furniture
32 Other manufacturing
33 Repair and installation of machinery and equipment
35 Electricity, gas, steam and air conditioning supply
36 Water collection, treatment and supply
37 Sewerage
38 Waste collection, treatment and disposal activities; materials recovery
39 Remediation activities and other waste management services
41 Construction of buildings
42 Civil engineering
43 Specialized construction activities
45 Wholesale and retail trade and repair of motor vehicles and motorcycles
46 Wholesale trade, except of motor vehicles and motorcycles
47 Retail trade, except of motor vehicles and motorcycles
49 Land transport and transport via pipelines
50 Water transport
51 Air transport
52 Warehousing and support activities for transportation
53 Postal and courier activities
55 Accommodation
56 Food and beverage service activities
58 Publishing activities
59 Motion picture, video and television programme production, sound recording and music publishing activities
60 Programming and broadcasting activities
61 Telecommunications
62 Computer programming, consultancy and related activities
63 Information service activities
64 Financial service activities, except insurance and pension funding
65 Insurance, reinsurance and pension funding, except compulsory social security
66 Activities auxiliary to financial service and insurance activities
68 Real estate activities
69 Legal and accounting activities
70 Activities of head offices; management consultancy activities
71 Architectural and engineering activities; technical testing and analysis
72 Scientific research and development
73 Advertising and market research
74 Other professional, scientific and technical activities
75 Veterinary activities
77 Rental and leasing activities
78 Employment activities
79 Travel agency, tour operator, reservation service and related activities
80 Security and investigation activities
81 Services to buildings and landscape activities
82 Office administrative, office support and other business support activities
84 Public administration and defence; compulsory social security
85 Education
86 Human health activities
87 Residential care activities
88 Social work activities without accommodation
90 Creative, arts and entertainment activities
91 Libraries, archives, museums and other cultural activities
92 Gambling and betting activities
93 Sports activities and amusement and recreation activities
94 Activities of membership organizations
95 Repair of computers and personal and household goods
96 Other personal service activities
97 Activities of households as employers of domestic personnel
98 Undifferentiated goods- and services-producing activities of private households for own use
99 Activities of extraterritorial organizations and bodies
""")

CPC_SECTIONS = _table("""
0 Agriculture, forestry and fishery products
1 Ores and minerals; electricity, gas and water
2 Food products, beverages and tobacco; textiles, apparel and leather products
3 Other transportable goods, except metal products, machinery and equipment
4 Metal products, machinery and equipment
5 Constructions and construction services
6 Distributive trade services; accommodation, food and beverage serving services; transport services; and electricity, gas and water distribution services
7 Financial and related services; real estate services; and rental and leasing services
8 Business and production services
9 Community, social and personal services
""")
