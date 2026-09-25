// SPDX-License-Identifier: MIT
pragma solidity 0.8.24;

/// @title Séquestre des cautions KORKO
/// @notice Le client bloque sa caution en AVAX de test à la réservation.
///         Au retour de la planche, KORKO prend le prix de la location et
///         le contrat rend le reste au client. Si la planche n'est pas
///         revenue avant 23 h, KORKO saisit toute la caution.
/// @dev Aucune donnée personnelle : une caution n'est connue que par un
///      identifiant de location aléatoire et l'adresse qui l'a bloquée.
contract SequestreKorko {
    struct Caution {
        address client;
        bool reglee;
        uint256 montant;
    }

    /// @notice Le compte du cloud KORKO, seul à régler les cautions.
    address public immutable korko;

    mapping(bytes32 => Caution) private _cautions;

    /// @notice Un client a bloqué sa caution pour une location.
    event CautionBloquee(
        bytes32 indexed location,
        address indexed client,
        uint256 montant
    );

    /// @notice La location est payée sur la caution, le reste est rendu.
    event LocationPayee(bytes32 indexed location, uint256 paye, uint256 rendu);

    /// @notice La planche n'est pas revenue à temps : KORKO garde tout.
    event CautionSaisie(bytes32 indexed location, uint256 montant);

    error NonAutorise(address appelant);
    error MontantNul();
    error LocationExistante(bytes32 location);
    error CautionInconnue(bytes32 location);
    error CautionReglee(bytes32 location);
    error MontantExcessif(uint256 montantDu, uint256 caution);
    error VirementRefuse(address destinataire);

    modifier seulementKorko() {
        if (msg.sender != korko) revert NonAutorise(msg.sender);
        _;
    }

    constructor() {
        korko = msg.sender;
    }

    /// @notice Bloque la valeur envoyée comme caution de la location.
    /// @param location identifiant aléatoire de la location
    function bloquer(bytes32 location) external payable {
        if (msg.value == 0) revert MontantNul();
        if (_cautions[location].client != address(0)) {
            revert LocationExistante(location);
        }
        _cautions[location] = Caution(msg.sender, false, msg.value);
        emit CautionBloquee(location, msg.sender, msg.value);
    }

    /// @notice Paie la location sur la caution et rend le reste au client.
    /// @param montantDu prix de la location en wei, au plus la caution
    function cloturer(bytes32 location, uint256 montantDu)
        external
        seulementKorko
    {
        Caution storage caution = _regler(location);
        if (montantDu > caution.montant) {
            revert MontantExcessif(montantDu, caution.montant);
        }
        uint256 rendu = caution.montant - montantDu;
        _virer(korko, montantDu);
        _virer(caution.client, rendu);
        emit LocationPayee(location, montantDu, rendu);
    }

    /// @notice Garde toute la caution : la planche n'est pas revenue à temps.
    function saisir(bytes32 location) external seulementKorko {
        Caution storage caution = _regler(location);
        _virer(korko, caution.montant);
        emit CautionSaisie(location, caution.montant);
    }

    /// @notice L'état d'une caution : client, réglée ou non, montant en wei.
    function lireCaution(bytes32 location)
        external
        view
        returns (Caution memory)
    {
        return _cautions[location];
    }

    /// @dev Marque la caution réglée avant tout virement : elle ne peut
    ///      être réglée qu'une fois, même par un appel réentrant.
    function _regler(bytes32 location)
        private
        returns (Caution storage caution)
    {
        caution = _cautions[location];
        if (caution.client == address(0)) revert CautionInconnue(location);
        if (caution.reglee) revert CautionReglee(location);
        caution.reglee = true;
    }

    /// @dev Un virement nul est sauté ; un virement refusé annule tout.
    function _virer(address destinataire, uint256 montant) private {
        if (montant == 0) return;
        (bool reussi, ) = destinataire.call{value: montant}("");
        if (!reussi) revert VirementRefuse(destinataire);
    }
}
