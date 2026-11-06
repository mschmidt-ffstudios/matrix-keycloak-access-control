import java.nio.file.Files;
import java.nio.file.LinkOption;
import java.nio.file.Path;
import java.security.KeyStore;
import java.security.MessageDigest;
import java.security.cert.X509Certificate;
import java.util.HashSet;
import java.util.HexFormat;
import java.util.Set;
import javax.net.ssl.TrustManagerFactory;
import javax.net.ssl.X509TrustManager;

/** Checks the reviewed Red Hat CA store through the runtime's default JSSE path. */
public class VerifyDefaultTrust {
    private static final Path VENDOR_CA_STORE = Path.of("/etc/pki/ca-trust/extracted/java/cacerts");
    private static final String REVIEWED_STORE_SHA256 =
        "ae293832a4330eed9d6f234f76c38671c0c6908fbfcfa90b979f9182f413d5eb";
    private static final Set<String> AFFECTED_CERTIFICATES = Set.of(
        "d7a7a0fb5d7e2731d771e9484ebcdef71d5f0c3e0a2948782bc83ee0ea699ef4", // AAA root
        "86a1ecba089c4a8d3bbe2734c612ba341d813e043cf9e8a862cd5c57a36bbe6b", // emSign G3 root
        "45a883f349aedfd5f3a649b5498ffb121712ba8b448f3f2a91dbc5a766fcddbe"  // AAA cross-sign
    );

    public static void main(String[] args) throws Exception {
        Path security = Path.of(System.getProperty("java.home"), "lib", "security");
        if (!Files.notExists(security.resolve("jssecacerts"), LinkOption.NOFOLLOW_LINKS)
                || System.getProperty("javax.net.ssl.trustStore") != null) {
            throw new IllegalStateException("JSSE has a higher-priority trust source");
        }
        Path storePath = security.resolve("cacerts").toRealPath();
        if (!storePath.equals(VENDOR_CA_STORE)
                || !REVIEWED_STORE_SHA256.equals(sha256(Files.readAllBytes(storePath)))) {
            throw new IllegalStateException("Java does not use the reviewed vendor CA store");
        }

        KeyStore store = KeyStore.getInstance(storePath.toFile(), "changeit".toCharArray());
        Set<String> expected = new HashSet<>();
        for (var aliases = store.aliases(); aliases.hasMoreElements();) {
            expected.add(sha256(store.getCertificate(aliases.nextElement()).getEncoded()));
        }
        TrustManagerFactory factory = TrustManagerFactory.getInstance(TrustManagerFactory.getDefaultAlgorithm());
        factory.init((KeyStore) null);
        Set<String> accepted = new HashSet<>();
        for (var manager : factory.getTrustManagers()) {
            if (manager instanceof X509TrustManager x509) {
                for (X509Certificate certificate : x509.getAcceptedIssuers()) {
                    accepted.add(sha256(certificate.getEncoded()));
                }
            }
        }
        if (accepted.isEmpty() || !accepted.equals(expected) || accepted.stream().anyMatch(AFFECTED_CERTIFICATES::contains)) {
            throw new IllegalStateException("Default JSSE anchors fail the reviewed CA policy");
        }
        System.out.println("Reviewed Keycloak CA store verified against " + accepted.size() + " default JSSE anchors");
    }

    private static String sha256(byte[] bytes) throws Exception {
        return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(bytes));
    }
}
